from django.core.management import BaseCommand

from apps.telegram_bot.bot_app import telegram_bot_app
from apps.telegram_bot.register_handlers import register_handlers


class Command(BaseCommand):
    def handle(self, *args, **options):
        register_handlers()
        telegram_bot_app.run_polling()
