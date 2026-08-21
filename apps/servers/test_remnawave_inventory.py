"""Инвентарь control plane после переезда: L0 обязан остаться зрячим.

Проба L0 — предохранитель против «оплатил, доступа нет». Если инвентарь начнёт
молча возвращать пустоту или частичную страницу, тревога не сработает именно
тогда, когда она нужна.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from apps.servers.remnawave_inventory import (
    fetch_control_plane_client_ids,
    fetch_inbound_snapshots,
)


_API = dict(REMNAWAVE_API_URL='https://panel.test', REMNAWAVE_API_TOKEN='t' * 32,
            XUI_CONTROL_PLANE_READ_ATTEMPTS=2, XUI_CONTROL_PLANE_READ_BACKOFF=0.0)


def _server():
    return SimpleNamespace(id=1, name='Нидерланды')


def _user(uuid: str, status: str = 'ACTIVE', short: str = 'abcd'):
    return {'vlessUuid': uuid, 'status': status, 'shortUuid': short}


def _profiles(*inbounds):
    return {'configProfiles': [{'config': {'inbounds': list(inbounds)}}], 'total': 1}


def _inbound(tag: str, port: int, network: str, security: str):
    return {'tag': tag, 'protocol': 'vless', 'port': port,
            'streamSettings': {'network': network, 'security': security}}


def _responder(users_pages, profiles):
    """Отдаёт страницы пользователей по порядку и один и тот же профиль."""
    pages = list(users_pages)

    async def _request(method, path, **kwargs):
        if path.startswith('/api/users'):
            return pages.pop(0) if pages else {'users': [], 'total': 0}
        return profiles

    return _request


class InventoryShapeTests(SimpleTestCase):
    @override_settings(**_API)
    async def test_snapshot_reports_transport_of_every_inbound(self):
        users = {'users': [_user('u1'), _user('u2', status='DISABLED')], 'total': 2}
        profiles = _profiles(
            _inbound('tcp', 8443, 'tcp', 'reality'),
            _inbound('grpc', 8080, 'grpc', 'reality'),
        )
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(
                side_effect=_responder([users, users], profiles))
            rows = await fetch_inbound_snapshots(_server())

        self.assertEqual([(row.port, row.network, row.security) for row in rows],
                         [(8080, 'grpc', 'reality'), (8443, 'tcp', 'reality')])
        self.assertTrue(all(row.clients == 2 for row in rows))
        self.assertTrue(all(row.enabled_clients == 1 for row in rows))

    @override_settings(**_API, SPECIAL_MONITOR_EXPECTED_INBOUNDS=[
        {'server_id': 1, 'inbound_id': 5, 'port': 8443},
        {'server_id': 1, 'inbound_id': 10, 'port': 8080},
    ])
    async def test_inbound_id_follows_the_port_not_the_order_in_the_profile(self):
        """Перестановка inbound-ов в профиле — не дрейф инвентаря, а порядок ключей."""
        users = {'users': [_user('u1')], 'total': 1}
        reordered = _profiles(
            _inbound('grpc', 8080, 'grpc', 'reality'),
            _inbound('tcp', 8443, 'tcp', 'reality'),
        )
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(
                side_effect=_responder([users, users], reordered))
            rows = await fetch_inbound_snapshots(_server())

        self.assertEqual({(row.inbound_id, row.port) for row in rows},
                         {(10, 8080), (5, 8443)})

    @override_settings(**_API, SPECIAL_MONITOR_EXPECTED_INBOUNDS=[])
    async def test_unexpected_inbound_is_identified_by_its_port(self):
        users = {'users': [_user('u1')], 'total': 1}
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(
                side_effect=_responder([users, users],
                                       _profiles(_inbound('x', 20443, 'xhttp', 'none'))))
            rows = await fetch_inbound_snapshots(_server())

        self.assertEqual([(row.inbound_id, row.port) for row in rows], [(20443, 20443)])

    @override_settings(**_API)
    async def test_disabled_client_is_not_counted_as_entitled_access(self):
        """Отключённый в панели не должен выглядеть как выданный доступ."""
        users = {'users': [_user('live'), _user('cut', status='DISABLED')], 'total': 2}
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(
                side_effect=_responder([users, users], _profiles()))
            all_ids, enabled_ids = await fetch_control_plane_client_ids(_server())

        self.assertEqual(all_ids, {'live', 'cut'})
        self.assertEqual(enabled_ids, {'live'})


class PaginationTests(SimpleTestCase):
    @override_settings(**_API)
    async def test_every_page_is_read_before_anyone_is_called_missing(self):
        """Оборванная выборка выглядит как исчезнувшие клиенты — это ложная тревога."""
        first = {'users': [_user(f'u{i}') for i in range(500)], 'total': 501}
        second = {'users': [_user('u500')], 'total': 501}
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(
                side_effect=_responder([first, second, first, second], _profiles()))
            all_ids, enabled_ids = await fetch_control_plane_client_ids(_server())

        self.assertEqual(len(all_ids), 501)
        self.assertIn('u500', enabled_ids)


class ConsistencyTests(SimpleTestCase):
    @override_settings(**_API)
    async def test_two_disagreeing_reads_raise_instead_of_reporting_drift(self):
        shrinking = {'users': [_user('a'), _user('b')], 'total': 2}
        smaller = {'users': [_user('a')], 'total': 1}
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(
                side_effect=_responder([shrinking, smaller], _profiles()))
            with self.assertRaises(RuntimeError):
                await fetch_control_plane_client_ids(_server())

    @override_settings(**_API)
    async def test_dead_panel_raises_so_the_probe_fails_closed(self):
        with patch('apps.servers.remnawave_inventory.RemnawaveAPI') as api_class:
            api_class.return_value.request_json = AsyncMock(side_effect=OSError('panel down'))
            with self.assertRaises(OSError):
                await fetch_inbound_snapshots(_server())
