from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.servers.models import Server


async def get_reply_markup_list_servers() -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=server.name, callback_data=f"select_server:{server.id}")]
        async for server in Server.objects.all()
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
