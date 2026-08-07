from apps.servers.vpn_client import APIVPNClient
from apps.vpn.models import UserVPN


async def disable_vpn_user_from_server(user_vpn: UserVPN):
    """Disable access while preserving the DB row and UUID for reactivation."""
    await APIVPNClient(user_vpn.server).enable_user(user_vpn, enabled=False)
    user_vpn.enabled = False
    await user_vpn.asave(update_fields=['enabled', 'updated_at'])


async def remove_vpn_user_from_server(user_vpn: UserVPN):
    """Permanently delete a user-owned key after explicit user action."""
    await APIVPNClient(user_vpn.server).remove_user(user_vpn)
    await user_vpn.adelete()
