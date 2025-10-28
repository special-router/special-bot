import asyncio

from django.core.management import BaseCommand

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.telegram_bot.bot_app import telegram_bot_app
from apps.telegram_bot.register_handlers import register_handlers
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.remove_vpn_user_from_server import remove_vpn_user_from_server


async def bla():
    async for user_vpn in (
            UserVPN.objects.with_related_user(
                TelegramUser.objects.all().annotate_balance(),
            )
            .with_related_server()
            .filter_by_enabled(False)
    ):
        print(user_vpn.id, user_vpn)
        await remove_vpn_user_from_server(user_vpn)
    # server = await Server.objects.aget()
    # user = await UserVPN.objects.with_related_user().afirst()
    # await APIVPNClient(server).get_key(user)

class Command(BaseCommand):
    def handle(self, *args, **options):
        # update_user_vpn()
        #asyncio.run(bla())
        register_handlers()
        telegram_bot_app.run_polling()
