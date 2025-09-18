from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.servers.models import Server


async def get_reply_markup_list_servers() -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=server.name, callback_data=f"select_server:{server.id}")]
        async for server in Server.objects.all()
    ]

    buttons += [
        [InlineKeyboardButton(text='👤 Мой профиль', callback_data='profile')],
        [InlineKeyboardButton(text='👁 Инструкция', callback_data='faq')],
        [InlineKeyboardButton(text='👨🏻‍🔧Тех.поддержка', url='https://t.me/Special_Wifi_Official')],
        [InlineKeyboardButton(text='Сотрудничество', url='https://t.me/nu_magich')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
