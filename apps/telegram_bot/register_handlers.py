from telegram import BotCommand
from telegram.ext import CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters

from apps.telegram_bot.bot_app import telegram_bot_app
from apps.telegram_bot.handlers.balance import show_balance
from apps.telegram_bot.handlers.start import start
from apps.telegram_bot.handlers.select_server import select_server
from apps.telegram_bot.handlers.top_up_balance import top_up_balance_promo, top_up_balance_one_month, \
    pre_checkout_callback, successful_payment_callback


def register_handlers():
    # commands
    telegram_bot_app.add_handler(CommandHandler("start", start))
    telegram_bot_app.add_handler(CommandHandler("balance", show_balance))

    # await telegram_bot_app.bot.set_my_commands(
    #     [
    #         BotCommand('start', 'Показать список серверов'),
    #         BotCommand('balance', 'Показать баланс'),
    #     ]
    # )

    # callbacks
    telegram_bot_app.add_handler(CallbackQueryHandler(select_server, pattern=r"^select_server:\d+$"))
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_promo, pattern=r"^top_up_balance_promo$"))
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_one_month, pattern=r"^top_up_balance_one_month"))

    telegram_bot_app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    telegram_bot_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
