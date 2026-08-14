import contextlib
import logging

from django.conf import settings
from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from apps.telegram_bot.catalog import acatalog


logger = logging.getLogger(__name__)


async def post_init_handler(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand('start', 'Главное меню'),
            BotCommand('balance', 'Баланс и пополнение'),
        ]
    )
    # Список стран лежит в кэше процесса, а бот — отдельный процесс от того,
    # что отдаёт подписки, так что после запуска кэш здесь пуст. Первым за него
    # заплатил бы ожиданием первый нажавший /start; пусть платит запуск.
    # Провайдер отвечает под общим дедлайном в восемь секунд, и неудача стоит
    # ровно этого прогрева — экран потом просто посчитает каталог сам.
    with contextlib.suppress(Exception):
        await acatalog()
    logger.info('subscription catalog warmed')


telegram_bot_app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).post_init(post_init_handler).build()
