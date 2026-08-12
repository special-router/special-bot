import contextlib
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update

from apps.analytics.balance_split import split_balance
from apps.users.models import TelegramUser


logger = logging.getLogger(__name__)

# Расшифровка показывается только тому, у кого бонус есть: пустая строка «бонусов
# 0 руб.» занимает место на каждом экране и не отвечает ни на один вопрос.
BONUS_LINE = 'В том числе бонусных: {amount} руб. — они списываются первыми.'


def payments_enabled() -> bool:
    """Есть ли платёжный провайдер, к которому можно отправить счёт.

    Живёт здесь, а не рядом с пополнением: экран оплаты и его клавиатура оба
    спрашивают об этом, а импорт друг друга замкнул бы их в цикл.

    На пустом токене Bot API отвечает `Payment_provider_invalid`, то есть
    кнопка суммы обещает то, чего бот сделать не может.
    """
    return bool(getattr(settings, 'YOUMONEY_TOKEN', ''))


async def balance_state_lines(user: TelegramUser) -> list[str]:
    """Строка баланса и, если есть бонус, его расшифровка.

    Итоговое число не меняется ничем: пользователю по-прежнему показывается
    ``balance``, а разложение только объясняет, из чего оно состоит. Разложение
    считается по журналу и на экране не обязано быть: сбой в нём оставляет экран
    с прежней строкой баланса, а не без баланса.
    """
    lines = [f'Баланс: {user.balance} руб.']

    if not getattr(settings, 'BALANCE_SPLIT_UI_ENABLED', True):
        return lines

    try:
        split = await sync_to_async(split_balance)(user.id)
    except Exception:
        logger.warning('balance_split_unavailable user_id=%s', user.id, exc_info=True)
        return lines

    if split.bonus > 0:
        lines.append(BONUS_LINE.format(amount=split.bonus))

    return lines


async def get_referral_user(update: Update) -> TelegramUser | None:
    with contextlib.suppress(IndexError):
        if telegram_id_user := update.message.text.split(' ')[1]:
            return await TelegramUser.objects.filter(telegram_id=telegram_id_user).afirst()


async def get_user(update: Update, referral_user: TelegramUser | None = None) -> TelegramUser:
    from_user = update.callback_query.from_user if update.callback_query else update.message.from_user

    username = from_user.username or f'user_{from_user.id}'
    user, _ = await TelegramUser.objects.aget_or_create(
        telegram_id=from_user.id,
        defaults={
            'username': username,
            'referral_user': referral_user,
        },
    )

    user: TelegramUser = (
        await TelegramUser.objects.annotate_balance().with_related_referral_user().aget(telegram_id=user.telegram_id)
    )

    return user
