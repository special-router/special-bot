from telegram import Update
from telegram.ext import ContextTypes

from apps.subscriptions.models import RouterOrder
from apps.telegram_bot.handlers.router.keyboards import special_router_menu_keyboard
from apps.telegram_bot.utils import get_user


SPECIAL_ROUTER_TEXT = """
📡 **Special Router**

Подписка на роутер **SPECIAL Mini** — доступ к VPN через ваше устройство.

• Активируйте роутер по серийному номеру со стикера
• Продлевайте подписку в «Управление подпиской»
• Тарифы: от 500 ₽ / 5 USDT в месяц

Поддержка: @Special_Wifi_Official
"""



async def special_router_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)
    await context.bot.send_message(
        user.telegram_id,
        text=SPECIAL_ROUTER_TEXT,
        parse_mode='Markdown',
        reply_markup=special_router_menu_keyboard(),
    )


async def router_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_user(update)

    lines = ['📜 **История заказов**\n']
    async for order in RouterOrder.objects.filter(user=user).order_by('-created_at')[:15]:
        date = order.created_at.strftime('%d.%m.%Y %H:%M')
        device = order.device.display_id if order.device_id else '—'
        lines.append(f'• {date} — {order.get_order_type_display()} — {order.amount} {order.currency} — {device}')

    if len(lines) == 1:
        lines.append('Пока нет заказов.')

    from apps.telegram_bot.handlers.router.keyboards import back_main
    from telegram import InlineKeyboardMarkup

    await context.bot.send_message(
        user.telegram_id,
        text='\n'.join(lines),
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_main()]),
    )
