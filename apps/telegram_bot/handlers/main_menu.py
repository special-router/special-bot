from typing import Final

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.telegram_bot.catalog import acatalog, catalog_body
from apps.telegram_bot.inline_buttons.start import get_reply_markup_main_menu
from apps.telegram_bot.ui import render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


WELCOME_BODY: Final[str] = (
    'Подписка списывается с баланса посуточно и включает пять устройств без доплаты.\n\n'
    'Пополните баланс, добавьте подписку — ссылка появится в разделе «Подписки». '
    'Новым пользователям в разделе «Оплата» доступны 7 дней бесплатно.'
)


async def build_main_menu_screen(user: TelegramUser, *, greeting: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """Главный экран. Баланс и число подписок вынесены сюда, чтобы за ними не
    приходилось открывать профиль.

    Для действующей подписки показывается её фактический набор стран. Без неё
    остаётся витринный каталог: экран не должен обещать канареечные профили
    пользователю, которому они ещё не назначены.
    """
    active_connections = (
        UserVPN.objects.with_related_server()
        .filter_by_user(user_id=user.id)
        .filter_by_enabled(True)
    )
    active_keys = await active_connections.acount()
    first_connection = await active_connections.order_by('created_at', 'id').afirst()
    catalog = await acatalog(first_connection)

    text = screen(
        'SPECIAL VPN',
        state=[f'Баланс: {user.balance} руб.', f'Активных подписок: {active_keys}'],
        body=[*([WELCOME_BODY] if greeting else []), *catalog_body(catalog)],
    )

    return text, await get_reply_markup_main_menu()


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_main_menu_screen(user)
    await render_screen(update, context, text, keyboard)
