from apps.servers.models import Server
from apps.servers.vpn_client import APIVPNClient
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


async def add_vpn_to_user(user: TelegramUser, server: Server) -> UserVPN:
    user_vpn: UserVPN = await UserVPN.objects.acreate(
        user=user,
        server=server,
    )

    user_vpn = await UserVPN.objects.with_related_user().with_related_server().aget(id=user_vpn.id)

    await APIVPNClient(server).add_user(user_vpn)
    user_vpn.vpn_key = await APIVPNClient(server).get_key(user_vpn)
    await user_vpn.asave()

    return user_vpn
