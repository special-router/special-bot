from django.conf import settings
from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder


async def post_init_handler(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand('start', 'Главное меню'),
            BotCommand('balance', 'Баланс и пополнение'),
        ]
    )


telegram_bot_app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).post_init(post_init_handler).build()
