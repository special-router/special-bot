from django.conf import settings
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.telegram_bot.inline_buttons.profile import get_reply_markup_profile
from apps.telegram_bot.ui import code, render_screen, screen
from apps.telegram_bot.utils import balance_state_lines, get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


async def build_profile_screen(user: TelegramUser) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка счёта: деньги, счётчик подписок, идентификатор для поддержки.

    Ссылки подписок сюда намеренно не попадают — они живут на экране
    «Подписки», где рядом с ними лежат действия, и дублировать их означало бы
    два места, которые расходятся при любой правке.
    """
    user_with_balance = await TelegramUser.objects.annotate_balance().aget(id=user.id)

    active_keys = await UserVPN.objects.filter_by_user(user_id=user.id).filter_by_enabled(True).acount()

    text = screen(
        'Профиль',
        state=[
            *await balance_state_lines(user_with_balance),
            'Подписка активна' if active_keys else 'Подписка не подключена',
            f'ID: {code(user.telegram_id)}',
        ],
        body=[f'Вы с нами с {user.created_at.strftime("%d.%m.%Y")}.'],
    )

    return text, await get_reply_markup_profile()


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_profile_screen(user)
    await render_screen(update, context, text, keyboard)
