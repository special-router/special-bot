"""Перенос на Remnawave: то, что должно пережить переключение без правок у клиента."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import SynchronousOnlyOperation
from django.test import SimpleTestCase, override_settings

from apps.servers.remnawave import RemnawaveAPI, RemnawaveError, configured
from apps.servers.remnawave_client import (
    RemnawaveVPNClient,
    panel_identity,
    remnawave_username,
)
from apps.servers.vpn_client import APIVPNClient, vpn_client_for


_UUID = '11111111-2222-3333-4444-555555555555'
_REALITY = dict(
    REMNAWAVE_REALITY_PUBLIC_KEY='p' * 43,
    REMNAWAVE_REALITY_SERVER_NAME='example.test',
    REMNAWAVE_REALITY_SHORT_ID='aabb',
    REMNAWAVE_REALITY_PORT=443,
)
_API = dict(REMNAWAVE_API_URL='https://panel.test', REMNAWAVE_API_TOKEN='t' * 32)


def _server():
    return SimpleNamespace(id=1, name='Нидерланды', client_vpn_host='203.0.113.10:443',
                           vpn_url='https://old.test', vpn_username='u', vpn_password='p',
                           inbound_id=5)


def _user_vpn(**overrides):
    base = dict(id=807, vpn_uuid=_UUID, sub_id='a' * 32, enabled=True, device_limit=None,
                server=_server(), user=SimpleNamespace(telegram_id=6847813966))
    base.update(overrides)
    return SimpleNamespace(**base)


class _LazyRelation:
    """Запись, чья связь ``user`` резолвится только в синхронном потоке.

    Так ведёт себя ``UserVPN``, вынутый без ``select_related('user')``: доступ
    к связи из async-контекста Django запрещает.
    """

    id = 807

    @property
    def user(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return SimpleNamespace(telegram_id=6847813966)
        raise SynchronousOnlyOperation('You cannot call this from an async context')


class ClientSelectionTests(SimpleTestCase):
    @override_settings(REMNAWAVE_ENABLED=False)
    def test_disabled_flag_keeps_every_request_on_the_old_panel(self):
        self.assertIsInstance(vpn_client_for(_server()), APIVPNClient)

    @override_settings(REMNAWAVE_ENABLED=True, **_API)
    def test_enabled_flag_switches_without_touching_the_call_sites(self):
        self.assertIsInstance(vpn_client_for(_server()), RemnawaveVPNClient)


class IdentityTests(SimpleTestCase):
    def test_username_separates_two_records_of_the_same_person(self):
        first = remnawave_username(_user_vpn(id=807))
        second = remnawave_username(_user_vpn(id=808))
        self.assertNotEqual(first, second)
        self.assertNotIn(' ', first)

    @override_settings(REMNAWAVE_ENABLED=True, **_API)
    async def test_panel_calls_survive_an_unloaded_user_relation(self):
        """Биллинг и отключение работают с записями, где ``user`` не подгружен.

        Обращение к ленивой связи из async-контекста бросает
        ``SynchronousOnlyOperation``, а вызывающий код читает это как аварию
        панели: оплативший остался бы без ссылки, неоплативший — включённым.
        """
        username, telegram_id = await panel_identity(_LazyRelation())

        self.assertEqual(telegram_id, 6847813966)
        self.assertEqual(username, 'tg_6847813966_807')

    @override_settings(REMNAWAVE_ENABLED=True, **_API, **_REALITY)
    async def test_issued_link_keeps_the_uuid_the_customer_already_has(self):
        """Ссылка, скопированная клиентом до миграции, должна поднимать ту же личность."""
        link = await RemnawaveVPNClient(_server()).get_key(_user_vpn())

        self.assertIn(_UUID, link)
        self.assertIn('security=reality', link)
        self.assertIn('sni=example.test', link)
        # Порт берётся из client_vpn_host, а не из настройки: перед узлом стоит
        # релей, и клиент набирает его, а не сам узел.
        self.assertIn('@203.0.113.10:443', link)

    @override_settings(REMNAWAVE_ENABLED=True, **_API,
                       REMNAWAVE_REALITY_PUBLIC_KEY='', REMNAWAVE_REALITY_SERVER_NAME='')
    async def test_missing_reality_parameters_fail_loudly(self):
        """Пустой ключ дал бы ссылку, которая выглядит рабочей и не подключается."""
        with self.assertRaises(RemnawaveError):
            await RemnawaveVPNClient(_server()).get_key(_user_vpn())


@override_settings(**_API)
class CreatePayloadTests(SimpleTestCase):
    async def _capture(self, **settings_overrides):
        captured = {}

        async def fake_request(self, method, path, *, json_body=None, allow_404=False):
            if method == 'GET':
                return None
            captured.update({'method': method, 'path': path, 'body': json_body})
            return {
                'id': 2,
                'vlessUuid': json_body['vlessUuid'],
                'telegramId': json_body['telegramId'],
                'shortUuid': json_body['shortUuid'],
            }

        with patch.object(RemnawaveAPI, '_request', fake_request):
            with override_settings(**settings_overrides):
                await RemnawaveVPNClient(_server()).add_user(_user_vpn())
        return captured

    async def test_short_uuid_equals_our_sub_id(self):
        """Иначе панель раздаёт подписку по своему идентификатору, а прокси ходит по нашему."""
        captured = await self._capture()

        self.assertEqual(captured['body']['shortUuid'], 'a' * 32)
        self.assertEqual(captured['body']['vlessUuid'], _UUID)

    async def test_squad_field_name_follows_the_setting(self):
        """Контракт панели переименовывал это поле между версиями."""
        captured = await self._capture(REMNAWAVE_SQUAD_UUIDS=['squad-1'],
                                       REMNAWAVE_SQUAD_FIELD='squadUuids')

        self.assertEqual(captured['body']['squadUuids'], ['squad-1'])
        self.assertNotIn('activeInternalSquads', captured['body'])

    async def test_no_squads_configured_sends_no_squad_field(self):
        """Пустой список — это «панель решит сама», а не «во все отряды»."""
        captured = await self._capture(REMNAWAVE_SQUAD_UUIDS=[])

        self.assertNotIn('activeInternalSquads', captured['body'])

    async def test_traffic_is_unlimited_because_the_bot_bills_by_days(self):
        captured = await self._capture()

        self.assertEqual(captured['body']['trafficLimitBytes'], 0)


@override_settings(REMNAWAVE_ENABLED=True, **_API)
class IdentityFieldTests(SimpleTestCase):
    """Панель 3.x опознаёт пользователя целочисленным ``id``; поля ``uuid`` нет."""

    async def _capture_patch(self, existing: dict):
        captured = {}

        async def fake_request(self, method, path, *, json_body=None, allow_404=False):
            if method == 'GET':
                return existing
            captured.update({'method': method, 'path': path, 'body': json_body})
            return existing

        with patch.object(RemnawaveAPI, '_request', fake_request):
            await RemnawaveVPNClient(_server()).enable_user(_user_vpn(), enabled=False)
        return captured

    async def test_status_change_addresses_the_user_by_id(self):
        captured = await self._capture_patch({
            'id': 42,
            'username': 'x',
            'vlessUuid': _UUID,
            'telegramId': 6847813966,
            'shortUuid': 'a' * 32,
        })

        self.assertEqual(captured['method'], 'PATCH')
        self.assertEqual(captured['body']['id'], 42)
        self.assertEqual(captured['body']['status'], 'DISABLED')
        # Запрос с ``uuid`` панель отвергает на валидации.
        self.assertNotIn('uuid', captured['body'])

    async def test_delete_addresses_the_user_by_id(self):
        captured = {}

        async def fake_request(self, method, path, *, json_body=None, allow_404=False):
            if method == 'GET':
                return {'id': 42}
            captured.update({'method': method, 'path': path})
            return None

        with patch.object(RemnawaveAPI, '_request', fake_request):
            await RemnawaveVPNClient(_server()).remove_user(_user_vpn())

        self.assertEqual((captured['method'], captured['path']), ('DELETE', '/api/users/42'))


class DisableTests(SimpleTestCase):
    @override_settings(REMNAWAVE_ENABLED=True, **_API)
    async def test_disabling_an_absent_client_does_not_create_one(self):
        """Заведение клиента ради отключения выдало бы доступ там, где его убирают."""
        calls = []

        async def fake_request(self, method, path, *, json_body=None, allow_404=False):
            calls.append(method)
            return None

        with patch.object(RemnawaveAPI, '_request', fake_request):
            await RemnawaveVPNClient(_server()).enable_user(_user_vpn(), enabled=False)

        self.assertEqual(calls, ['GET'])


class ConfigurationTests(SimpleTestCase):
    @override_settings(REMNAWAVE_API_URL='', REMNAWAVE_API_TOKEN='')
    def test_unconfigured_panel_is_reported_not_guessed(self):
        self.assertFalse(configured())
        with self.assertRaises(RemnawaveError):
            RemnawaveAPI()
