from telegram import Update
from telegram.ext import ContextTypes

from apps.servers.subscription_connector import SubscriptionClientMissing, SubscriptionConnectorDisabled
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_subscription_url


async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deliver an existing 3x-ui URL without changing legacy key delivery."""
    user: TelegramUser = await get_user(update)
    connection = (
        await UserVPN.objects.with_related_server().filter(user=user, enabled=True).order_by('created_at').afirst()
    )
    if connection is None:
        await context.bot.send_message(user.telegram_id, text='У вас нет активной подписки.')
        return

    try:
        subscription_url = await get_subscription_url(connection)
    except (SubscriptionClientMissing, SubscriptionConnectorDisabled):
        await context.bot.send_message(
            user.telegram_id,
            text='Подписка ещё не подготовлена. Используйте ранее выданную ссылку подключения.',
        )
        return

    await context.bot.send_message(
        user.telegram_id,
        text=f'URL подписки (автообновление конфигурации):\n\n{subscription_url}',
    )
