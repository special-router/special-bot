from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import UUID

from apps.servers.vpn_client import APIVPNClient
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user
from apps.vpn.services.remove_vpn_user_from_server import disable_vpn_user_from_server


class APIVPNClientStateTests(IsolatedAsyncioTestCase):
    def make_user_vpn(self):
        return SimpleNamespace(
            vpn_uuid=UUID('00000000-0000-0000-0000-000000000003'),
            user=SimpleNamespace(telegram_id=1003),
            server=SimpleNamespace(inbound_id=5),
        )

    @patch('apps.servers.vpn_client.AsyncApi')
    async def test_enable_adds_missing_control_plane_client_with_same_uuid(self, api_class):
        server = SimpleNamespace(
            vpn_url='https://panel.invalid/',
            vpn_username='user',
            vpn_password='password',
            inbound_id=5,
        )
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_by_id = AsyncMock(return_value=SimpleNamespace(settings=SimpleNamespace(clients=[])))
        api.client.add = AsyncMock()
        client = APIVPNClient(server)
        user_vpn = self.make_user_vpn()

        await client.enable_user(user_vpn, enabled=True)

        api.client.add.assert_awaited_once()
        inbound_id, clients = api.client.add.await_args.args
        self.assertEqual(inbound_id, 5)
        self.assertEqual(str(clients[0].id), str(user_vpn.vpn_uuid))
        self.assertTrue(clients[0].enable)

    @patch('apps.servers.vpn_client.AsyncApi')
    async def test_enable_updates_existing_control_plane_client(self, api_class):
        server = SimpleNamespace(
            vpn_url='https://panel.invalid/',
            vpn_username='user',
            vpn_password='password',
            inbound_id=5,
        )
        user_vpn = self.make_user_vpn()
        existing = SimpleNamespace(id=str(user_vpn.vpn_uuid), enable=False)
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_by_id = AsyncMock(return_value=SimpleNamespace(settings=SimpleNamespace(clients=[existing])))
        api.client.update = AsyncMock()
        api.client.add = AsyncMock()

        await APIVPNClient(server).enable_user(user_vpn, enabled=True)

        self.assertTrue(existing.enable)
        api.client.update.assert_awaited_once_with(str(user_vpn.vpn_uuid), existing)
        api.client.add.assert_not_awaited()

    @patch('apps.servers.vpn_client.AsyncApi')
    async def test_disable_missing_control_plane_client_is_idempotent(self, api_class):
        server = SimpleNamespace(
            vpn_url='https://panel.invalid/',
            vpn_username='user',
            vpn_password='password',
            inbound_id=5,
        )
        api = api_class.return_value
        api.login = AsyncMock()
        api.inbound.get_by_id = AsyncMock(return_value=SimpleNamespace(settings=SimpleNamespace(clients=[])))
        api.client.add = AsyncMock()
        api.client.update = AsyncMock()

        await APIVPNClient(server).enable_user(self.make_user_vpn(), enabled=False)

        api.client.add.assert_not_awaited()
        api.client.update.assert_not_awaited()


class AddVpnToUserTests(IsolatedAsyncioTestCase):
    @patch('apps.vpn.services.add_vpn_to_user.APIVPNClient')
    @patch('apps.vpn.services.add_vpn_to_user.UserVPN.objects')
    async def test_reactivation_reuses_disabled_record_and_uuid(self, objects, client_class):
        original_uuid = UUID('00000000-0000-0000-0000-000000000005')
        user = SimpleNamespace(id=10, telegram_id=1010)
        server = SimpleNamespace(id=20)
        disabled = SimpleNamespace(
            vpn_uuid=original_uuid,
            vpn_key='existing-key',
            enabled=False,
            user=None,
            server=None,
            asave=AsyncMock(),
        )
        related = objects.with_related_user.return_value.with_related_server.return_value
        by_user = related.filter_by_user.return_value
        by_server = by_user.filter_by_server.return_value
        query = by_server.filter_by_enabled.return_value
        query.afirst = AsyncMock(return_value=disabled)
        client_class.return_value.enable_user = AsyncMock()
        client_class.return_value.get_key = AsyncMock()

        result = await add_vpn_to_user(user, server)

        self.assertIs(result, disabled)
        self.assertEqual(result.vpn_uuid, original_uuid)
        self.assertTrue(result.enabled)
        objects.acreate.assert_not_called()
        client_class.return_value.enable_user.assert_awaited_once_with(disabled, enabled=True)
        client_class.return_value.get_key.assert_not_awaited()
        disabled.asave.assert_awaited_once_with(update_fields=['vpn_key', 'enabled', 'updated_at'])

    @patch('apps.vpn.services.add_vpn_to_user.APIVPNClient')
    @patch('apps.vpn.services.add_vpn_to_user.UserVPN.objects')
    async def test_new_record_stays_disabled_when_control_plane_fails(self, objects, client_class):
        user = SimpleNamespace(id=11, telegram_id=1011)
        server = SimpleNamespace(id=21)
        pending = SimpleNamespace(
            vpn_uuid=UUID('00000000-0000-0000-0000-000000000006'),
            vpn_key='',
            enabled=False,
        )
        related = objects.with_related_user.return_value.with_related_server.return_value
        by_user = related.filter_by_user.return_value
        by_server = by_user.filter_by_server.return_value
        lookup = by_server.filter_by_enabled.return_value
        lookup.afirst = AsyncMock(return_value=None)
        objects.acreate = AsyncMock(return_value=SimpleNamespace(id=30))
        objects.with_related_user.return_value.with_related_server.return_value.aget = AsyncMock(return_value=pending)
        client_class.return_value.enable_user = AsyncMock(side_effect=RuntimeError('panel unavailable'))

        with self.assertRaisesRegex(RuntimeError, 'panel unavailable'):
            await add_vpn_to_user(user, server)

        objects.acreate.assert_awaited_once_with(user=user, server=server, enabled=False)
        self.assertFalse(pending.enabled)


class DisableVpnUserTests(IsolatedAsyncioTestCase):
    @patch('apps.vpn.services.remove_vpn_user_from_server.APIVPNClient')
    async def test_disable_preserves_record_and_uuid(self, client_class):
        original_uuid = UUID('00000000-0000-0000-0000-000000000004')
        user_vpn = SimpleNamespace(
            vpn_uuid=original_uuid,
            server=object(),
            enabled=True,
            asave=AsyncMock(),
            adelete=AsyncMock(),
        )
        client_class.return_value.enable_user = AsyncMock()

        await disable_vpn_user_from_server(user_vpn)

        client_class.return_value.enable_user.assert_awaited_once_with(user_vpn, enabled=False)
        self.assertFalse(user_vpn.enabled)
        self.assertEqual(user_vpn.vpn_uuid, original_uuid)
        user_vpn.asave.assert_awaited_once_with(update_fields=['enabled', 'updated_at'])
        user_vpn.adelete.assert_not_awaited()
