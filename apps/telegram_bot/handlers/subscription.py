from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.servers.subscription_connector import SubscriptionClientMissing, SubscriptionConnectorDisabled
from apps.telegram_bot.inline_buttons.back import get_reply_markup_back
from apps.telegram_bot.ui import code, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_subscription_url


async def build_subscription_screen(user: TelegramUser) -> tuple[str, InlineKeyboardMarkup]:
    """Deliver an existing 3x-ui URL without changing legacy key delivery."""
    connection = (
        await UserVPN.objects.with_related_server().filter(user=user, enabled=True).order_by('created_at').afirst()
    )
    if connection is None:
        body = ['У вас нет активной подписки.']
    else:
        try:
            body = [f'Адрес обновляет конфигурацию сам:\n{code(await get_subscription_url(connection))}']
        except (SubscriptionClientMissing, SubscriptionConnectorDisabled):
            body = ['Подписка ещё не подготовлена. Используйте ранее выданную ссылку подключения.']

    return screen('URL подписки', body=body), await get_reply_markup_back('show_keys')


async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_subscription_screen(user)
    await render_screen(update, context, text, keyboard)
