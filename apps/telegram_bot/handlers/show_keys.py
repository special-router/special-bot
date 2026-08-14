from typing import Final

from django.conf import settings
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.telegram_bot.catalog import acatalog, catalog_body
from apps.telegram_bot.inline_buttons.manage_keys import get_reply_markup_manage_keys
from apps.telegram_bot.ui import STATUS_ACTIVE, STATUS_INACTIVE, code, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_user_access_url


COPY_HINT: Final[str] = (
    'Нажмите на ссылку — она скопируется, — и вставьте её в приложение. '
    'Одна подписка работает на двух устройствах.'
)

EMPTY_HINT: Final[str] = 'Подписок пока нет. Нажмите «Добавить» — спишется стоимость одних суток.'


async def build_keys_screen(user: TelegramUser, *, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Единственный экран, где живут ссылки подписок и действия над ними.

    `notice` — результат только что выполненного действия (привязка, сброс).
    Он показывается здесь, а не отдельным сообщением: у отдельного сообщения не
    было клавиатуры, и пользователь оставался в тупике.
    """
    entries: list[str] = []
    active_keys = 0
    first_connection = None

    async for vpn_connection in UserVPN.objects.with_related_server().filter(user=user):
        if vpn_connection.enabled:
            active_keys += 1
        if first_connection is None:
            first_connection = vpn_connection

        marker = STATUS_ACTIVE if vpn_connection.enabled else STATUS_INACTIVE
        access_url = await get_user_access_url(vpn_connection)
        # Имя сервера здесь стояло как название страны и называло одну — ту, где
        # стоит панель. Стран в подписке давно больше, и они перечислены ниже
        # одним списком: повторять в каждой строке нечего, а называть одну из
        # девяти значило бы прятать остальные восемь.
        entries.append(
            f'{marker} Подписка, с {vpn_connection.created_at.strftime("%d.%m.%Y")}\n'
            f'{code(access_url)}'
        )

    catalog = await acatalog(first_connection)

    text = screen(
        'Подписки',
        state=[
            f'Баланс: {user.balance} руб.',
            f'Активных подписок: {active_keys} из {settings.MAX_KEYS}',
        ],
        body=[notice, *entries, *catalog_body(catalog), COPY_HINT if entries else EMPTY_HINT],
    )

    return text, await get_reply_markup_manage_keys(user)


async def show_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_keys_screen(user)
    await render_screen(update, context, text, keyboard)
