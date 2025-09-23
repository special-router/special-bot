import asyncio

from django.core.management import BaseCommand

from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.telegram_bot.bot_app import telegram_bot_app
from apps.telegram_bot.register_handlers import register_handlers
from apps.vpn.models import UserVPN


async def bla():
    # todo:
    server = await Server.objects.aget()
    user = await UserVPN.objects.with_related_user().afirst()
    await APIVPNClient(server).get_key(user)


class Command(BaseCommand):
    def handle(self, *args, **options):
        # update_user_vpn()
        #asyncio.run(bla())
        register_handlers()
        telegram_bot_app.run_polling()
