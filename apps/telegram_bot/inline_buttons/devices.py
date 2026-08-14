from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.telegram_bot import icons
from apps.telegram_bot.ui import back_button, button


async def get_reply_markup_devices(devices, *, can_drop: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура экрана устройств.

    Отвязка идёт отдельной кнопкой на каждое устройство, а не одной на все:
    номер в подписи совпадает с номером в списке выше, поэтому нажимающий
    видит, что именно он убирает. Общей кнопки «отвязать всё» здесь нет: в
    списке есть каждое устройство, включая безымянное, так что случая «не
    понимаю, что отвязывать» не остаётся.

    «Убрать место» появляется только когда есть платное место: на бесплатных
    она отказывала бы в том, чего не обещала.
    """
    buttons: list[list[InlineKeyboardButton]] = [
        [
            button('Добавить место', 'add_device_slot', icon=icons.KEY),
            *([button('Убрать место', 'drop_device_slot', icon=icons.TRASH)] if can_drop else []),
        ],
    ]

    buttons += [
        [button(f'Отвязать {index}', f'unbind_device:{device.id}', icon=icons.REFRESH)]
        for index, device in enumerate(devices, start=1)
    ]

    buttons += [[back_button('show_keys')]]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
