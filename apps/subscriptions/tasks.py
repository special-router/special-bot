import asyncio

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import TariffServer
from apps.servers.vpn_client import APIVPNClient
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


def update_user_vpn():
    for user_vpn in (
        UserVPN.objects.with_related_user(
            TelegramUser.objects.all().annotate_balance(),
        )
        .with_related_server()
        .filter_by_enabled(True)
    ):
        tariff: TariffServer = TariffServer.objects.get()

        Transaction.objects.create(
            user=user_vpn.user,
            amount=-tariff.price,
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.EVERYDAY_SYSTEM,
        )

        if user_vpn.user.balance - tariff.price < tariff.price:
            user_vpn.enabled = False
            user_vpn.save()

        asyncio.run(APIVPNClient(user_vpn.server).enable_user(user_vpn, True))
