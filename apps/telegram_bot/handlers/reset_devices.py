from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.devices import reset_devices as reset_user_devices
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


RESET_DONE_TEXT = (
    'Устройства отвязаны от подписки.\n\n'
    'Откройте приложение на тех устройствах, которыми пользуетесь — они привяжутся заново.'
)


async def reset_devices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Let the user re-bind their devices themselves after changing a phone."""
    user: TelegramUser = await get_user(update)

    done, remaining = await sync_to_async(reset_user_devices)(user.id)
    if not done:
        await context.bot.send_message(
            user.telegram_id,
            text=(
                'Сбросить устройства можно раз в сутки.\n\n'
                f'Повторите попытку через {_humanized(remaining)}.'
            ),
        )
        return

    await context.bot.send_message(user.telegram_id, text=RESET_DONE_TEXT)


def _humanized(remaining) -> str:
    """Render the cooldown as a wait, since the bot has no user time zone."""
    minutes = max(int(remaining.total_seconds()) // 60, 1)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f'{minutes} мин.'
    return f'{hours} ч. {minutes} мин.'
