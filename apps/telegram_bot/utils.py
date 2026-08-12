import contextlib

from django.conf import settings
from telegram import Update

from apps.users.models import TelegramUser


def payments_enabled() -> bool:
    """Есть ли платёжный провайдер, к которому можно отправить счёт.

    Живёт здесь, а не рядом с пополнением: экран оплаты и его клавиатура оба
    спрашивают об этом, а импорт друг друга замкнул бы их в цикл.

    На пустом токене Bot API отвечает `Payment_provider_invalid`, то есть
    кнопка суммы обещает то, чего бот сделать не может.
    """
    return bool(getattr(settings, 'YOUMONEY_TOKEN', ''))


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
