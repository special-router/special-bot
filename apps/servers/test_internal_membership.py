from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from django.test import override_settings

from apps.servers.internal_membership import (
    InternalMembershipSyncError,
    configured_internal_targets,
    sync_internal_memberships,
)


ENDPOINTS = [
    {'inbound_id': 7, 'advertised_port': 39329, 'label': 'one'},
    {'inbound_id': 9, 'advertised_port': 46517, 'label': 'two'},
    {'inbound_id': 13, 'advertised_port': 27914, 'label': 'three'},
    {'inbound_id': 10, 'advertised_port': 80, 'label': 'four'},
]


class InternalMembershipSyncTests(IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._sync_settings = override_settings(
            SUBSCRIPTION_INTERNAL_MEMBERSHIP_SYNC_ENABLED=True)
        self._sync_settings.enable()
        self.addCleanup(self._sync_settings.disable)

    def user(self, user_id=801):
        return SimpleNamespace(id=user_id, vpn_uuid='synthetic-uuid')

    def api(self, by_target):
        return SimpleNamespace(
            inbound=SimpleNamespace(get_by_id=AsyncMock(side_effect=lambda target: by_target[target])),
            client=SimpleNamespace(update=AsyncMock()),
        )

    def inbound(self, *clients):
        return SimpleNamespace(settings=SimpleNamespace(clients=list(clients)))

    def member(self, *, identity='synthetic-uuid', enabled=True):
        return SimpleNamespace(id=identity, enable=enabled, expiry_time=0)

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=ENDPOINTS)
    async def test_disable_and_renewal_update_only_existing_fixed_targets(self):
        members = {target: self.member() for target in (7, 9, 13, 10)}
        api = self.api({target: self.inbound(member) for target, member in members.items()})

        await sync_internal_memberships(api, self.user(), enabled=False, expiry_time=123)

        self.assertEqual(api.client.update.await_count, 4)
        self.assertEqual({call.args[1].inbound_id for call in api.client.update.await_args_list}, {7, 9, 13, 10})
        self.assertTrue(all(not member.enable and member.expiry_time == 123 for member in members.values()))

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=ENDPOINTS)
    async def test_missing_or_duplicate_membership_fails_closed_without_writes(self):
        api = self.api({
            7: self.inbound(self.member()), 9: self.inbound(),
            13: self.inbound(self.member(), self.member()), 10: self.inbound(self.member()),
        })

        with self.assertRaisesRegex(InternalMembershipSyncError, 'internal_membership_sync_failed'):
            await sync_internal_memberships(api, self.user(), enabled=True)

        api.client.update.assert_not_awaited()
        api.client.add = AsyncMock()
        api.client.add.assert_not_awaited()

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=ENDPOINTS)
    async def test_partial_panel_read_failure_is_aggregate_and_no_create(self):
        api = self.api({target: self.inbound(self.member()) for target in (7, 9, 13, 10)})
        api.inbound.get_by_id.side_effect = [self.inbound(self.member()), RuntimeError(), self.inbound(self.member()), self.inbound(self.member())]

        with self.assertRaises(InternalMembershipSyncError):
            await sync_internal_memberships(api, self.user(), enabled=True)

        api.client.update.assert_not_awaited()

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=ENDPOINTS)
    async def test_non_test_user_is_untouched(self):
        api = self.api({})
        await sync_internal_memberships(api, self.user(802), enabled=False)
        api.inbound.get_by_id.assert_not_awaited()

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=ENDPOINTS)
    def test_policy_returns_only_validated_retained_set(self):
        self.assertEqual({target.inbound_id for target in configured_internal_targets(801)}, {7, 9, 13, 10})

    @override_settings(SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=True,
                       SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[801],
                       SUBSCRIPTION_INTERNAL_ENDPOINTS=ENDPOINTS[:-1])
    def test_partial_endpoint_config_is_not_a_sync_policy(self):
        self.assertEqual(configured_internal_targets(801), ())
