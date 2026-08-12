from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.handlers.show_keys import show_keys
from apps.telegram_bot.inline_buttons.remove_keys import get_reply_markup_remove_keys
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.remove_vpn_user_from_server import remove_vpn_user_from_server


async def show_keys_for_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user: TelegramUser = await get_user(update)

    await context.bot.send_message(
        user.telegram_id,
        text='Подписки, доступные для удаления:',
        parse_mode='Markdown',
        reply_markup=await get_reply_markup_remove_keys(user),
    )


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
        return

    await remove_vpn_user_from_server(user_vpn)

    await update.callback_query.answer(
        text='Подписка успешно удалена',
    )

    await show_keys(update, context)
