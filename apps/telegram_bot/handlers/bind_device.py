from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.devices import open_binding_window
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.ui import render_screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


async def bind_device(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the window in which a new device may attach itself to the подписка.

    The subscription link is a bearer URL, so the request to add a device has to
    arrive here, where Telegram vouches for who is asking.

    No keyboard offers this any more — binding happens by itself when a client
    fetches the subscription.  The handler stays because screens already sent to
    users still carry the `bind_device` callback, and an unhandled press is a
    button that spins forever.
    """
    user: TelegramUser = await get_user(update)

    window = await sync_to_async(open_binding_window)(user.id)

    notice = (
        f'Привязка нового устройства открыта на {_humanized(window)}.\n\n'
        'Откройте приложение на новом устройстве и обновите подписку — оно привяжется само. '
        'Уже привязанные устройства работают всегда, отдельно открывать привязку для них не нужно.'
    )

    text, keyboard = await build_keys_screen(user, notice=notice)
    await render_screen(update, context, text, keyboard)


def _humanized(window) -> str:
    minutes = max(int(window.total_seconds()) // 60, 1)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f'{minutes} мин.'
    return f'{hours} ч. {minutes} мин.'
