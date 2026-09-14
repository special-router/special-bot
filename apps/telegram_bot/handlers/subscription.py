from asgiref.sync import sync_to_async
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.servers.subscription_connector import SubscriptionClientMissing, SubscriptionConnectorDisabled
from apps.telegram_bot.inline_buttons.back import get_reply_markup_back
from apps.telegram_bot.ui import back_button, button, code, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_subscription_url
from apps.subscriptions.tokens import issue_router_activation_code


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

    keyboard = InlineKeyboardMarkup([
        [button('Код для роутера', 'router_activation')],
        [back_button('show_keys')],
    ])
    return screen('URL подписки', body=body), keyboard


async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_subscription_screen(user)
    await render_screen(update, context, text, keyboard)


async def router_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    connection = await UserVPN.objects.filter(user=user, enabled=True).order_by('created_at').afirst()
    if connection is None:
        text = screen('Роутер', body=['Сначала подключите подписку.'])
    else:
        raw_code, _record = await sync_to_async(issue_router_activation_code)(connection)
        text = screen('Роутер', body=[
            'Введите этот одноразовый код в роутере в течение 10 минут:',
            code(raw_code),
            'Роутер обменяет код на отдельный токен устройства. Основная VPN-ссылка не раскрывается.',
        ])
    await render_screen(update, context, text, await get_reply_markup_back('show_subscription'))
