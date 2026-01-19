from apps.servers.vpn_client import APIVPNClient
from apps.vpn.models import UserVPN


async def remove_vpn_user_from_server(user_vpn: UserVPN):
    await APIVPNClient(user_vpn.server).remove_user(user_vpn)
    await user_vpn.adelete()
