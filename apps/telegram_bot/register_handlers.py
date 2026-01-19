from telegram.ext import CallbackQueryHandler, CommandHandler, filters, MessageHandler, PreCheckoutQueryHandler

from apps.telegram_bot.bot_app import telegram_bot_app
from apps.telegram_bot.handlers.add_key import add_key
from apps.telegram_bot.handlers.balance import show_balance
from apps.telegram_bot.handlers.faq import faq
from apps.telegram_bot.handlers.main_menu import main_menu
from apps.telegram_bot.handlers.profile import show_profile
from apps.telegram_bot.handlers.referral import referral
from apps.telegram_bot.handlers.remove_key import remove_key, show_keys_for_remove
from apps.telegram_bot.handlers.show_keys import show_keys
from apps.telegram_bot.handlers.start import start
from apps.telegram_bot.handlers.top_up_balance import (
    pre_checkout_callback,
    successful_payment_callback,
    top_up_balance_one_month,
    top_up_balance_promo,
    top_up_balance_six_month,
    top_up_balance_three_month,
    top_up_balance_two_month,
    top_up_balance_year,
)


def register_handlers():
    # commands
    telegram_bot_app.add_handler(CommandHandler('start', start))
    telegram_bot_app.add_handler(CommandHandler('balance', show_balance))
    telegram_bot_app.add_handler(CommandHandler('faq', faq))

    # callbacks
    telegram_bot_app.add_handler(CallbackQueryHandler(remove_key, pattern=r'^remove_key:\d+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(show_balance, pattern=r'^show_balance$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_promo, pattern=r'^top_up_balance_promo$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_one_month, pattern=r'^top_up_balance_one_month'))
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_two_month, pattern=r'^top_up_balance_two_month'))
    telegram_bot_app.add_handler(
        CallbackQueryHandler(top_up_balance_three_month, pattern=r'^top_up_balance_three_month')
    )
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_six_month, pattern=r'^top_up_balance_six_month'))
    telegram_bot_app.add_handler(CallbackQueryHandler(top_up_balance_year, pattern=r'^top_up_balance_year'))
    telegram_bot_app.add_handler(CallbackQueryHandler(faq, pattern=r'^faq'))
    telegram_bot_app.add_handler(CallbackQueryHandler(show_profile, pattern=r'^profile$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(main_menu, pattern=r'^main_menu$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(show_keys, pattern=r'^show_keys$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(add_key, pattern=r'^add_key:\d+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(show_keys_for_remove, pattern=r'^show_keys_for_remove'))

    telegram_bot_app.add_handler(CallbackQueryHandler(referral, pattern=r'^referral$'))

    telegram_bot_app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    telegram_bot_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
