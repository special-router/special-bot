from django.conf import settings
from telegram.ext import ApplicationBuilder

telegram_bot_app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

