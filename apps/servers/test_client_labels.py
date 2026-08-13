import json
import logging
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from django.test import TestCase, override_settings
from py3xui import Client
from py3xui.async_api import AsyncClientApi

from apps.servers.client_labels import (
    LabelledClientApi,
    client_label,
    is_client_label,
    label_for_client,
    labelling_enabled,
    owner_for_uuid,
)
from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


UUID = '11111111-2222-3333-4444-555555555555'


class ClientLabelFormatTests(TestCase):
    def test_label_is_derived_from_user_vpn_id_and_inbound(self):
        self.assertEqual(client_label(5, 42), 'uv-5-42')

    def test_label_is_stable_across_repeated_writes(self):
        self.assertEqual(client_label(5, 42), client_label(5, 42))

    def test_only_our_own_format_counts_as_a_label(self):
        self.assertTrue(is_client_label('uv-5-42'))
        for foreign in ('', None, 'осталось 28 дней', 'keenetic1', 'uv-5', 'uv-a-42'):
            self.assertFalse(is_client_label(foreign), foreign)


@override_settings(CLIENT_TRAFFIC_LABELS_ENABLED=True)
class ClientLabelResolutionTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='test', price='7.00')
        self.server = Server.objects.create(
            name='test',
            ip_address='127.0.0.1',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='https://panel.invalid',
            client_vpn_host='127.0.0.1',
            tariff=tariff,
            inbound_id=5,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='test-user')
        self.connection = UserVPN.objects.create(user=self.user, server=self.server, enabled=True)

    def test_resolves_a_known_uuid_to_its_connection_and_its_inbound(self):
        self.assertEqual(owner_for_uuid(self.connection.vpn_uuid), (self.connection.id, 5))

    def test_unknown_and_malformed_ids_have_no_owner(self):
        self.assertIsNone(owner_for_uuid('11111111-2222-3333-4444-555555555555'))
        self.assertIsNone(owner_for_uuid('not-a-uuid'))
        self.assertIsNone(owner_for_uuid(None))

    def test_label_carries_no_customer_identity(self):
        label = label_for_client(SimpleNamespace(id=str(self.connection.vpn_uuid), email=''), 5)

        self.assertEqual(label, f'uv-5-{self.connection.id}')
        self.assertNotIn(str(self.user.telegram_id), label)
        self.assertNotIn(self.user.username, label)
        self.assertNotIn(str(self.connection.vpn_uuid), label)

    def test_client_without_a_connection_is_left_alone(self):
        self.assertIsNone(label_for_client(SimpleNamespace(id='keenetic1', email=''), 5))

    def test_foreign_label_is_never_overwritten(self):
        client = SimpleNamespace(id=str(self.connection.vpn_uuid), email='осталось 28 дней')

        self.assertIsNone(label_for_client(client, 5))

    def test_unscoped_write_is_left_alone(self):
        client = SimpleNamespace(id=str(self.connection.vpn_uuid), email='')

        self.assertIsNone(label_for_client(client, None))

    def test_an_inbound_we_do_not_own_gets_no_label(self):
        """Inbounds 10 and 13 host a foreign tenant; we do not write to them."""
        client = SimpleNamespace(id=str(self.connection.vpn_uuid), email='')

        for foreign_inbound in (7, 9, 10, 13, 14):
            self.assertIsNone(label_for_client(client, foreign_inbound), foreign_inbound)

    def test_the_inbound_we_own_is_read_from_configuration(self):
        client = SimpleNamespace(id=str(self.connection.vpn_uuid), email='')
        self.server.inbound_id = 9
        self.server.save(update_fields=['inbound_id'])

        self.assertEqual(label_for_client(client, 9), f'uv-9-{self.connection.id}')
        self.assertIsNone(label_for_client(client, 5))


class LabelledClientApiTests(IsolatedAsyncioTestCase):
    def setUp(self):
        # override_settings cannot decorate an IsolatedAsyncioTestCase class.
        labelling = override_settings(CLIENT_TRAFFIC_LABELS_ENABLED=True)
        labelling.enable()
        self.addCleanup(labelling.disable)

    def api(self):
        return object.__new__(LabelledClientApi)

    @staticmethod
    def client(email='', inbound_id=None):
        return SimpleNamespace(id=UUID, email=email, inbound_id=inbound_id)

    async def test_create_is_labelled(self):
        client = self.client()
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                patch.object(AsyncClientApi, 'add', new=AsyncMock()) as add:
            await self.api().add(5, [client])

        self.assertEqual(client.email, 'uv-5-42')
        add.assert_awaited_once()

    async def test_update_is_labelled_from_the_inbound_it_targets(self):
        client = self.client(inbound_id=5)
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()) as update:
            await self.api().update(client.id, client)

        self.assertEqual(client.email, 'uv-5-42')
        update.assert_awaited_once()

    async def test_repeated_writes_keep_the_same_label(self):
        client = self.client(inbound_id=5)
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()):
            await self.api().update(client.id, client)
            first = client.email
            await self.api().update(client.id, client)

        self.assertEqual(client.email, first)

    async def test_client_without_a_connection_is_written_unlabelled(self):
        client = self.client(inbound_id=5)
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=None), \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()) as update:
            await self.api().update(client.id, client)

        self.assertEqual(client.email, '')
        update.assert_awaited_once()

    async def test_labelling_does_not_change_the_update_url(self):
        """The label must travel in the body only; it cannot move a client's route."""
        api = self.api()
        api._post = AsyncMock()
        api._url = lambda endpoint: f'https://panel.invalid/{endpoint}'
        api.logger = logging.getLogger(__name__)
        client = Client(id=UUID, email='', enable=True, inbound_id=5)

        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)):
            await api.update(UUID, client)

        endpoint = api._post.await_args.args[0].removeprefix('https://panel.invalid/')
        self.assertEqual(endpoint, f'panel/api/inbounds/updateClient/{UUID}')
        self.assertEqual(json.loads(api._post.await_args.args[2]['settings'])['clients'][0]['email'], 'uv-5-42')

    async def test_labelling_does_not_change_the_add_url(self):
        api = self.api()
        api._post = AsyncMock()
        api._url = lambda endpoint: f'https://panel.invalid/{endpoint}'
        api.logger = logging.getLogger(__name__)

        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)):
            await api.add(5, [Client(id=UUID, email='', enable=True)])

        endpoint = api._post.await_args.args[0].removeprefix('https://panel.invalid/')
        self.assertEqual(endpoint, 'panel/api/inbounds/addClient')
        self.assertEqual(json.loads(api._post.await_args.args[2]['settings'])['clients'][0]['email'], 'uv-5-42')

    async def test_status_label_survives_a_write(self):
        client = self.client(email='осталось 28 дней', inbound_id=7)
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()):
            await self.api().update(client.id, client)

        self.assertEqual(client.email, 'осталось 28 дней')

    async def test_a_write_to_an_inbound_we_do_not_own_carries_no_label(self):
        for foreign_inbound in (7, 10, 13):
            client = self.client(inbound_id=foreign_inbound)
            with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                    patch.object(AsyncClientApi, 'update', new=AsyncMock()) as update:
                await self.api().update(client.id, client)

            self.assertEqual(client.email, '', foreign_inbound)
            update.assert_awaited_once()


class LabellingDisabledTests(IsolatedAsyncioTestCase):
    """Off is the default, and off must be byte-identical to the old behaviour."""

    @staticmethod
    def api():
        return object.__new__(LabelledClientApi)

    @staticmethod
    def client(email='', inbound_id=5):
        return SimpleNamespace(id=UUID, email=email, inbound_id=inbound_id)

    def test_off_is_the_default(self):
        self.assertFalse(labelling_enabled())

    async def test_update_leaves_the_email_untouched(self):
        client = self.client()
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()) as update:
            await self.api().update(client.id, client)

        self.assertEqual(client.email, '')
        update.assert_awaited_once()

    async def test_create_leaves_the_email_untouched(self):
        client = self.client()
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(42, 5)), \
                patch.object(AsyncClientApi, 'add', new=AsyncMock()) as add:
            await self.api().add(5, [client])

        self.assertEqual(client.email, '')
        add.assert_awaited_once()

    async def test_an_existing_label_is_neither_extended_nor_removed(self):
        client = self.client(email='uv-5-42')
        with patch('apps.servers.client_labels.owner_for_uuid', return_value=(99, 5)), \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()):
            await self.api().update(client.id, client)

        self.assertEqual(client.email, 'uv-5-42')

    async def test_the_owner_is_not_even_looked_up(self):
        """Off costs no query: the flag is checked before touching the database."""
        client = self.client()
        with patch('apps.servers.client_labels.owner_for_uuid') as owner, \
                patch.object(AsyncClientApi, 'update', new=AsyncMock()):
            await self.api().update(client.id, client)

        owner.assert_not_called()


@override_settings(CLIENT_TRAFFIC_LABELS_ENABLED=True)
class VPNClientLabelTests(TestCase):
    """The label reaches the panel through the real APIVPNClient call path."""

    def setUp(self):
        tariff = TariffServer.objects.create(name='test', price='7.00')
        self.server = Server.objects.create(
            name='test',
            ip_address='127.0.0.1',
            ssh_username='unused',
            ssh_password='unused',
            vpn_username='unused',
            vpn_password='unused',
            vpn_key='unused',
            vpn_url='https://panel.invalid',
            client_vpn_host='127.0.0.1',
            tariff=tariff,
            inbound_id=5,
        )
        self.user = TelegramUser.objects.create(telegram_id=1001, username='test-user')
        self.connection = UserVPN.objects.create(user=self.user, server=self.server, enabled=True)

    def _vpn_client(self, existing_clients):
        from apps.servers.vpn_client import APIVPNClient

        with patch('apps.servers.vpn_client.AsyncApi'):
            vpn_client = APIVPNClient(self.server)
        vpn_client._api.login = AsyncMock()
        vpn_client._api.inbound.get_by_id = AsyncMock(
            return_value=SimpleNamespace(settings=SimpleNamespace(clients=existing_clients))
        )
        vpn_client._api.client = object.__new__(LabelledClientApi)
        return vpn_client

    async def test_enable_labels_an_existing_client_on_update(self):
        existing = SimpleNamespace(id=str(self.connection.vpn_uuid), email='', enable=True, inbound_id=None)
        vpn_client = self._vpn_client([existing])

        with patch.object(AsyncClientApi, 'update', new=AsyncMock()) as update:
            await vpn_client.enable_user(self.connection, enabled=True)

        self.assertEqual(existing.email, f'uv-5-{self.connection.id}')
        update.assert_awaited_once()

    async def test_enable_labels_a_client_it_has_to_create(self):
        vpn_client = self._vpn_client([])

        with patch.object(AsyncClientApi, 'add', new=AsyncMock()) as add:
            await vpn_client.enable_user(self.connection, enabled=True)

        created = add.await_args.args[1][0]
        self.assertEqual(created.email, f'uv-5-{self.connection.id}')

    async def test_the_update_body_carries_the_inbound_id_not_null(self):
        """py3xui puts client.inbound_id in the body as 'id'; null there is not a route."""
        existing = Client(id=str(self.connection.vpn_uuid), email='', enable=True)
        vpn_client = self._vpn_client([existing])
        vpn_client._api.client._post = AsyncMock()
        vpn_client._api.client._url = lambda endpoint: f'https://panel.invalid/{endpoint}'
        vpn_client._api.client.logger = logging.getLogger(__name__)

        await vpn_client.enable_user(self.connection, enabled=True)

        self.assertEqual(vpn_client._api.client._post.await_args.args[2]['id'], 5)
