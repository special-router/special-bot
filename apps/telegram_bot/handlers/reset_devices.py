from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.devices import reset_devices as reset_user_devices
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.ui import render_screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


RESET_DONE_TEXT = (
    'Устройства отвязаны от подписки. Привязка открыта — откройте приложение на тех устройствах, '
    'которыми пользуетесь, и они привяжутся заново.'
)


async def reset_devices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Let the user re-bind their devices themselves after changing a phone."""
    user: TelegramUser = await get_user(update)

    done, remaining = await sync_to_async(reset_user_devices)(user.id)
    if done:
        notice = RESET_DONE_TEXT
    else:
        notice = (
            'Сбрасывать устройства можно не так часто. '
            f'Повторите попытку через {_humanized(remaining)}.\n\n'
            'Чтобы добавить ещё одно устройство, сбрасывать не нужно — нажмите «Привязать устройство».'
        )

    text, keyboard = await build_keys_screen(user, notice=notice)
    await render_screen(update, context, text, keyboard)


def _humanized(remaining) -> str:
    """Render the cooldown as a wait, since the bot has no user time zone."""
    minutes = max(int(remaining.total_seconds()) // 60, 1)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f'{minutes} мин.'
    return f'{hours} ч. {minutes} мин.'
