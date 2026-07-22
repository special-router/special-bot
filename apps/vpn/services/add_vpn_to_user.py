from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_builder import build_subscription_url


async def add_vpn_to_user(user: TelegramUser, server: Server, **kwargs) -> UserVPN:
    user_vpn: UserVPN = await UserVPN.objects.acreate(
        user=user,
        server=server,
    )

    user_vpn = await UserVPN.objects.with_related_user().with_related_server().aget(id=user_vpn.id)

    vpn_client = APIVPNClient(server)
    await vpn_client.add_user_to_inbounds(user_vpn)
    user_vpn.vless_links = await vpn_client.build_vless_links(user_vpn)
    user_vpn.vpn_key = build_subscription_url(user_vpn)
    await user_vpn.asave()

    return user_vpn
