from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.devices import reset_devices as reset_user_devices
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.ui import render_screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


# Названо тем, что происходит: места освобождаются, и занимает их то
# устройство, которое откроет подписку следующим. Слова «привязка открыта»
# описывали механику, которой пользователь не управляет.
RESET_DONE_TEXT = (
    'Устройства отвязаны от подписки. Места свободны: их займут устройства, которые откроют '
    'подписку следующими — просто откройте приложение там, где пользуетесь.'
)


async def reset_devices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Release this user's device slots, so a replaced phone can take one.

    Clearing alone would strand the user once the binding window becomes
    mandatory, so `reset_user_devices` opens that window in the same
    transaction — the unbind stays a single button either way.
    """
    user: TelegramUser = await get_user(update)

    done, remaining = await sync_to_async(reset_user_devices)(user.id)
    if done:
        notice = RESET_DONE_TEXT
        toast = 'Устройства отвязаны.'
    else:
        notice = (
            'Отвязывать устройства можно не так часто. '
            f'Повторите попытку через {_humanized(remaining)}.\n\n'
            'Уже привязанные устройства всё это время работают как обычно.'
        )
        toast = 'Отвязать пока нельзя.'

    text, keyboard = await build_keys_screen(user, notice=notice)
    await render_screen(update, context, text, keyboard, toast=toast)


def _humanized(remaining) -> str:
    """Render the cooldown as a wait, since the bot has no user time zone."""
    minutes = max(int(remaining.total_seconds()) // 60, 1)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f'{minutes} мин.'
    return f'{hours} ч. {minutes} мин.'
