from asgiref.sync import sync_to_async
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.analytics.funnel import subscription_removed
from apps.telegram_bot.handlers.show_keys import build_keys_screen
from apps.telegram_bot.inline_buttons.remove_keys import get_reply_markup_remove_keys
from apps.telegram_bot.ui import answer_query, render_screen, screen
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.remove_vpn_user_from_server import remove_vpn_user_from_server


async def build_remove_keys_screen(user: TelegramUser) -> tuple[str, InlineKeyboardMarkup]:
    """Отдельный экран под удаление остаётся подтверждением: сама кнопка удаляет
    подписку сразу и без второго вопроса."""
    text = screen(
        'Удаление подписки',
        state=['Подписка удаляется сразу, вернуть её нельзя. Остаток баланса сохраняется.'],
        body=['Выберите, какую подписку удалить.'],
    )

    return text, await get_reply_markup_remove_keys(user)


async def show_keys_for_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)
    text, keyboard = await build_remove_keys_screen(user)
    await render_screen(update, context, text, keyboard)


async def remove_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    user_vpn_id: int = int(update.callback_query.data.split(':')[1])

    user_vpn: UserVPN | None = (
        await UserVPN.objects.with_related_server()
        .with_related_user()
        .filter_by_user(user_id=user.id)
        .filter_by_id(user_vpn_id)
        .afirst()
    )

    if not user_vpn:
        # Экран удаления мог устареть — подписки уже нет. Молчание здесь
        # неотличимо от отказа удалять.
        await answer_query(update, 'Подписка не найдена.')
        return

    await remove_vpn_user_from_server(user_vpn)
    await sync_to_async(subscription_removed)(user.id, user_vpn_id)

    text, keyboard = await build_keys_screen(user, notice='Подписка удалена.')
    await render_screen(update, context, text, keyboard, toast='Подписка удалена.')
