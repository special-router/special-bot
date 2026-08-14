import html
from typing import Final

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.subscriptions.devices import device_limit_for
from apps.telegram_bot.catalog import acatalog, catalog_body
from apps.telegram_bot.inline_buttons.manage_keys import get_reply_markup_manage_keys
from apps.telegram_bot.ui import STATUS_ACTIVE, STATUS_INACTIVE, bold, code, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_delivery import get_user_access_url


COPY_HINT: Final[str] = (
    'Нажмите на ссылку — она скопируется, — и вставьте её в приложение. '
    'Сколько устройств она обслуживает — на экране «Устройства».'
)

EMPTY_HINT: Final[str] = 'Подписки пока нет. Нажмите «Подключить» — спишется стоимость одних суток.'

# Устройство, назвавшееся при привязке пустыми заголовками. Слот оно занимает
# такой же, как любое другое, поэтому в списке ему нужно имя.
UNNAMED_DEVICE: Final[str] = 'без названия'

DEVICES_EMPTY: Final[str] = 'ещё ни одного, подключите приложение'


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
            f'{code(access_url)}\n'
            f'{await _devices_line(vpn_connection)}'
        )

    catalog = await acatalog(first_connection)

    text = screen(
        'Подписки',
        state=[
            f'Баланс: {user.balance} руб.',
            'Подписка активна' if active_keys else 'Подписка не подключена',
        ],
        body=[notice, *entries, *catalog_body(catalog), COPY_HINT if entries else EMPTY_HINT],
    )

    return text, await get_reply_markup_manage_keys(connected=bool(active_keys))


async def _devices_line(vpn_connection) -> str:
    """Какие устройства заняли места этой подписки и сколько мест осталось.

    Раньше об этом не говорил ни один экран: кнопка «Отвязать» стояла рядом с
    подпиской, про которую нельзя было узнать, привязано ли к ней хоть что-то,
    — и нажимали её вслепую. Имена берутся из заголовков клиента, поэтому
    экранируются; идентификатор устройства не показывается никогда, он не для
    чтения человеком и опознаёт устройство за пределами этой подписки.
    """
    names = [
        html.escape(device.device_model or device.device_os or UNNAMED_DEVICE)
        async for device in vpn_connection.devices.order_by('first_seen_at')
    ]
    limit = device_limit_for(vpn_connection)
    return f'{bold(f"Устройства ({len(names)} из {limit}):")} ' + (', '.join(names) or DEVICES_EMPTY)


async def show_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_keys_screen(user)
    await render_screen(update, context, text, keyboard)
