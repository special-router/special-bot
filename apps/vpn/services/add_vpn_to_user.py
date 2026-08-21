from apps.servers.models import Server
from apps.servers.vpn_client import vpn_client_for
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


async def add_vpn_to_user(user: TelegramUser, server: Server) -> UserVPN:
    user_vpn = (
        await UserVPN.objects.with_related_user()
        .with_related_server()
        .filter_by_user(user_id=user.id)
        .filter_by_server(server.id)
        .order_by('created_at')
        .afirst()
    )

    if user_vpn is None:
        user_vpn = await UserVPN.objects.acreate(
            user=user,
            server=server,
            enabled=False,
        )
        user_vpn = await UserVPN.objects.with_related_user().with_related_server().aget(id=user_vpn.id)

    vpn_client = vpn_client_for(server)
    await vpn_client.enable_user(user_vpn, enabled=True)
    if not user_vpn.vpn_key:
        user_vpn.vpn_key = await vpn_client.get_key(user_vpn)
    user_vpn.enabled = True
    await user_vpn.asave(update_fields=['vpn_key', 'enabled', 'updated_at'])

    return user_vpn
