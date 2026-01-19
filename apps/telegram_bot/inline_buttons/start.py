from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def get_reply_markup_main_menu() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text='👤 Мой профиль', callback_data='profile')],
        [InlineKeyboardButton(text='👁 Инструкция', callback_data='faq')],
        [InlineKeyboardButton(text='👨🏻‍🔧Тех.поддержка', url='https://t.me/Special_Wifi_Official')],
        [InlineKeyboardButton(text='🤝Сотрудничество', url='https://t.me/nu_magich')],
        [InlineKeyboardButton(text='💵Оплата', callback_data='show_balance')],
        [InlineKeyboardButton(text='🧑‍💻Управление подпиской', callback_data='show_keys')],
        [InlineKeyboardButton(text='💰 Реферальная система', callback_data='referral')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
