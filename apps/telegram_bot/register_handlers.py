from django.conf import settings
from telegram.ext import CallbackQueryHandler, CommandHandler, filters, MessageHandler, PreCheckoutQueryHandler

from apps.telegram_bot.bot_app import telegram_bot_app
from apps.telegram_bot.error_handler import on_error
from apps.telegram_bot.handlers.add_key import add_key
from apps.telegram_bot.handlers.admin.broadcast import admin_broadcast, admin_broadcast_audience, admin_broadcast_cancel, admin_broadcast_send
from apps.telegram_bot.handlers.admin.client import admin_client, admin_client_view
from apps.telegram_bot.handlers.admin.common import ADMIN_PENDING_INPUT
from apps.telegram_bot.handlers.admin.menu import admin_command, admin_menu
from apps.telegram_bot.handlers.admin.money import (
    admin_credit_execute,
    admin_credit_start,
    admin_vpn_disable_confirm,
    admin_vpn_disable_execute,
    admin_vpn_issue_confirm,
    admin_vpn_issue_execute,
    admin_vpn_issue_start,
)
from apps.telegram_bot.handlers.admin.monitoring import admin_monitor, admin_monitor_layer
from apps.telegram_bot.handlers.admin.text_input import admin_text_input
from apps.telegram_bot.handlers.balance import show_balance
from apps.telegram_bot.handlers.bind_device import bind_device
from apps.telegram_bot.handlers.devices import (
    add_device_slot,
    drop_device_slot,
    show_devices,
    unbind_one_device,
)
from apps.telegram_bot.handlers.faq import faq
from apps.telegram_bot.handlers.main_menu import main_menu
from apps.telegram_bot.handlers.profile import show_profile
from apps.telegram_bot.handlers.referral import referral
from apps.telegram_bot.handlers.reset_devices import reset_devices
from apps.telegram_bot.handlers.show_keys import show_keys
from apps.telegram_bot.handlers.start import start
from apps.telegram_bot.handlers.subscription import show_subscription
from apps.telegram_bot.handlers.support import (
    SUPPORT_MESSAGE_FILTER,
    support_close,
    support_message,
    support_open,
    support_operator_reply,
)
from apps.telegram_bot.handlers.top_up_cryptobot import topup_card, topup_crypto_pay, topup_period_selected
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
    # Первым: без него любое исключение в любом обработчике ниже остаётся
    # только в логе, а пользователь видит бесконечные «часики» на кнопке.
    telegram_bot_app.add_error_handler(on_error)

    # commands
    telegram_bot_app.add_handler(CommandHandler('start', start))
    telegram_bot_app.add_handler(CommandHandler('balance', show_balance))
    telegram_bot_app.add_handler(CommandHandler('faq', faq))
    # Not in `post_init_handler`'s public command list on purpose — it still
    # works when typed, it is just not suggested to every user.
    telegram_bot_app.add_handler(CommandHandler('admin', admin_command))

    # admin panel
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_menu, pattern=r'^admin_menu$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_client, pattern=r'^admin_client$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_client_view, pattern=r'^admin_client_view:\d+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_monitor, pattern=r'^admin_monitor$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_monitor_layer, pattern=r'^admin_monitor_layer:\w+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_broadcast, pattern=r'^admin_broadcast$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_broadcast_audience, pattern=r'^admin_broadcast_audience:\w+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_broadcast_send, pattern=r'^admin_broadcast_send$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_broadcast_cancel, pattern=r'^admin_broadcast_cancel$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_credit_start, pattern=r'^admin_credit:\d+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_credit_execute, pattern=r'^admin_credit_execute$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_vpn_issue_start, pattern=r'^admin_vpn_issue:\d+$'))
    telegram_bot_app.add_handler(
        CallbackQueryHandler(admin_vpn_issue_confirm, pattern=r'^admin_vpn_issue_confirm:\d+:\d+$')
    )
    telegram_bot_app.add_handler(
        CallbackQueryHandler(admin_vpn_issue_execute, pattern=r'^admin_vpn_issue_execute:\d+:\d+$')
    )
    telegram_bot_app.add_handler(CallbackQueryHandler(admin_vpn_disable_confirm, pattern=r'^admin_vpn_disable:\d+$'))
    telegram_bot_app.add_handler(
        CallbackQueryHandler(admin_vpn_disable_execute, pattern=r'^admin_vpn_disable_execute:\d+$')
    )
    # Registered before the support text handler below: on the rare overlap —
    # an admin mid-prompt who also has an open support ticket — the admin flow
    # they explicitly started takes priority. `ADMIN_PENDING_INPUT` itself only
    # matches an admin with a pending prompt, so this never swallows an
    # ordinary customer's message.
    telegram_bot_app.add_handler(MessageHandler(ADMIN_PENDING_INPUT, admin_text_input))

    # callbacks
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
    # Подписку из бота больше не удаляют — она у аккаунта одна. Оба callback'а
    # остались в разосланных раньше сообщениях, поэтому нажатие уводит на
    # экран подписок: без обработчика кнопка крутилась бы до таймаута.
    telegram_bot_app.add_handler(CallbackQueryHandler(show_keys, pattern=r'^show_keys_for_remove'))
    telegram_bot_app.add_handler(CallbackQueryHandler(show_keys, pattern=r'^remove_key:\d+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(show_devices, pattern=r'^show_devices$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(add_device_slot, pattern=r'^add_device_slot$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(drop_device_slot, pattern=r'^drop_device_slot$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(unbind_one_device, pattern=r'^unbind_device:\d+$'))
    # Кнопки привязки в клавиатуре больше нет, но она осталась в сообщениях,
    # разосланных раньше: без обработчика такое нажатие уходит в никуда.
    telegram_bot_app.add_handler(CallbackQueryHandler(bind_device, pattern=r'^bind_device$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(reset_devices, pattern=r'^reset_devices$'))
    if settings.SUBSCRIPTION_DELIVERY_ENABLED:
        telegram_bot_app.add_handler(CommandHandler('subscription', show_subscription))
        telegram_bot_app.add_handler(CallbackQueryHandler(show_subscription, pattern=r'^show_subscription$'))

    telegram_bot_app.add_handler(CallbackQueryHandler(referral, pattern=r'^referral$'))

    # Без чата операторов обращения внутри бота недоступны целиком: меню
    # оставляет прежнюю внешнюю ссылку, а обработчик текста не регистрируется —
    # бот не начинает вычитывать личную переписку ради выключенной функции.
    if settings.SUPPORT_CHAT_ID:
        telegram_bot_app.add_handler(CallbackQueryHandler(support_open, pattern=r'^support_open$'))
        telegram_bot_app.add_handler(CallbackQueryHandler(support_close, pattern=r'^support_close:\d+$'))
        # Фильтр перечисляет и то, что пересылается, и то, что отклоняется:
        # необработанное вложение иначе не дошло бы ни до какого обработчика, и
        # отправитель не узнал бы, что его сообщение никуда не ушло.
        telegram_bot_app.add_handler(
            MessageHandler(
                filters.Chat(settings.SUPPORT_CHAT_ID) & SUPPORT_MESSAGE_FILTER,
                support_operator_reply,
            )
        )
        telegram_bot_app.add_handler(MessageHandler(filters.ChatType.PRIVATE & SUPPORT_MESSAGE_FILTER, support_message))

    telegram_bot_app.add_handler(CallbackQueryHandler(topup_period_selected, pattern=r'^topup_period:\d+$'))
    telegram_bot_app.add_handler(CallbackQueryHandler(topup_card, pattern=r'^topup_card:\d+$'))
    if settings.CRYPTOBOT_TOKEN:
        telegram_bot_app.add_handler(
            CallbackQueryHandler(topup_crypto_pay, pattern=r'^topup_crypto_pay:\d+:\d+$')
        )

    telegram_bot_app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    telegram_bot_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
