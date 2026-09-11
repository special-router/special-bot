from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.providers.adapters import (
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderInventory,
    ProviderLeasePayload,
    ProviderOperationUnsupported,
    adapter_for_provider,
    register_adapter,
    unregister_adapter,
)
from apps.providers.choices import (
    CompiledSnapshotStateChoices,
    ProviderCredentialModeChoices,
    ProviderInventoryStateChoices,
    ProviderLeaseStateChoices,
    ProviderProbeStateChoices,
)
from apps.providers.models import (
    CompiledSnapshot,
    Provider,
    ProviderInventoryVersion,
    ProviderLease,
    ProviderProbeResult,
)
from apps.providers.services.inventory import (
    CanonicalEndpoint,
    active_inventory,
    promote_inventory,
    stage_inventory,
)
from apps.providers.services.snapshots import publish_snapshot, revoke_snapshots
from apps.providers.services.storage import SnapshotArtifact
from apps.providers.tasks import refresh_provider_inventory
from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


SOURCE_DIGEST = 'a' * 64
PAYLOAD_DIGEST = 'b' * 64
CONTENT_DIGEST = 'c' * 64
INVENTORY_DIGEST = 'd' * 64


class FakeSnapshotStore:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def inspect(self, storage_key):
        return self.artifacts.get(storage_key)


def canonical_endpoint(external_id='endpoint-1'):
    return CanonicalEndpoint(
        external_id=external_id,
        protocol='vless',
        transport='tcp',
        security='reality',
        host='VPN.Example',
        port=443,
        region='nl',
        server_name='SNI.Example',
        public_key='synthetic-public-key',
        short_id='a1b2c3d4',
        fingerprint='chrome',
    )


class ProviderAdapterContractTests(SimpleTestCase):
    def tearDown(self):
        unregister_adapter('test-api')

    def test_unknown_adapter_fails_closed(self):
        provider = SimpleNamespace(adapter='test-api')

        with self.assertRaisesMessage(ProviderConfigurationError, 'adapter_not_registered'):
            adapter_for_provider(provider)

    def test_default_lifecycle_operation_is_unsupported(self):
        class ReadOnlyAdapter(ProviderAdapter):
            @property
            def capabilities(self):
                return ProviderCapabilities(credential_mode='shared', protocols=('vless',))

            def fetch_inventory(self):
                raise AssertionError('not called')

        register_adapter('test-api', ReadOnlyAdapter)
        adapter = adapter_for_provider(SimpleNamespace(
            adapter='test-api',
            credential_mode='shared',
            per_user_lifecycle=False,
            usage_tracking=False,
        ))

        with self.assertRaisesMessage(ProviderOperationUnsupported, 'create_lease'):
            adapter.create_lease(SimpleNamespace())

    def test_duplicate_registration_is_rejected(self):
        class ReadOnlyAdapter(ProviderAdapter):
            @property
            def capabilities(self):
                return ProviderCapabilities(credential_mode='shared', protocols=('vless',))

            def fetch_inventory(self):
                raise AssertionError('not called')

        register_adapter('test-api', ReadOnlyAdapter)

        with self.assertRaisesMessage(ProviderConfigurationError, 'adapter_registration_conflict'):
            register_adapter('test-api', ReadOnlyAdapter)

    def test_declared_lifecycle_must_match_adapter_capabilities(self):
        class ReadOnlyAdapter(ProviderAdapter):
            @property
            def capabilities(self):
                return ProviderCapabilities(credential_mode='per_user', protocols=('vless',))

            def fetch_inventory(self):
                raise AssertionError('not called')

        register_adapter('test-api', ReadOnlyAdapter)
        provider = SimpleNamespace(
            adapter='test-api',
            credential_mode='per_user',
            per_user_lifecycle=True,
            usage_tracking=False,
        )

        with self.assertRaisesMessage(ProviderConfigurationError, 'adapter_lifecycle_incomplete'):
            adapter_for_provider(provider)

    def test_lease_payload_rejects_raw_provider_url(self):
        with self.assertRaisesMessage(ProviderConfigurationError, 'lease_secret_reference_invalid'):
            ProviderLeasePayload(
                external_id='lease-1',
                secret_reference='https://provider.example/sub/bearer',
            )


class ProviderDatabaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        tariff = TariffServer.objects.create(name='provider-test', price='7.00')
        server = Server.objects.create(
            name='provider-test',
            ip_address='198.51.100.10',
            ssh_username='ssh',
            ssh_password='ssh',
            vpn_username='panel',
            vpn_password='panel',
            vpn_key='key',
            tariff=tariff,
            inbound_id=5,
        )
        user = TelegramUser.objects.create(telegram_id=9191, username='provider-test')
        cls.subscription = UserVPN.objects.create(user=user, server=server, sub_id='provider-test')

    def create_provider(self, **overrides):
        values = {
            'slug': 'vendor-a',
            'name': 'Vendor A',
            'adapter': 'test-api',
            'credential_mode': ProviderCredentialModeChoices.PER_USER,
            'enabled': True,
            'per_user_lifecycle': True,
            'usage_tracking': True,
            'resale_confirmed_at': timezone.now(),
            'capabilities': {'allowed_endpoint_hosts': ['vpn.example']},
        }
        values.update(overrides)
        return Provider.objects.create(**values)


class ProviderModelTests(ProviderDatabaseTestCase):
    def test_a_service_and_vpnstar_are_seeded_disabled(self):
        providers = {
            provider.slug: provider
            for provider in Provider.objects.filter(slug__in=('a-service', 'vpnstar'))
        }

        self.assertEqual(set(providers), {'a-service', 'vpnstar'})
        self.assertEqual(providers['a-service'].adapter, 'a-service')
        self.assertEqual(providers['vpnstar'].adapter, 'vpnstar')
        self.assertFalse(providers['a-service'].enabled)
        self.assertFalse(providers['vpnstar'].enabled)

    def test_production_ready_requires_resale_and_isolated_lifecycle(self):
        provider = self.create_provider()

        self.assertTrue(provider.production_ready)

        provider.credential_mode = ProviderCredentialModeChoices.SHARED
        self.assertFalse(provider.production_ready)

    def test_shared_provider_is_not_production_ready(self):
        provider = self.create_provider(
            credential_mode=ProviderCredentialModeChoices.SHARED,
            per_user_lifecycle=False,
        )

        self.assertFalse(provider.production_ready)

    def test_active_lease_requires_external_and_secret_references(self):
        provider = self.create_provider()

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProviderLease.objects.create(
                provider=provider,
                subscription=self.subscription,
                state=ProviderLeaseStateChoices.ACTIVE,
            )


class ProviderInventoryTests(ProviderDatabaseTestCase):
    def setUp(self):
        self.provider = self.create_provider()

    def publish(self, endpoint=None, **overrides):
        values = {
            'provider_id': self.provider.id,
            'source_digest': SOURCE_DIGEST,
            'payload_digest': PAYLOAD_DIGEST,
            'endpoints': (endpoint or canonical_endpoint(),),
            'fetched_at': timezone.now(),
            'expires_at': timezone.now() + timedelta(hours=1),
        }
        values.update(overrides)
        return stage_inventory(**values)

    def promote(self, inventory, vantages=('bot',), allow_canary=False):
        for endpoint in inventory.endpoints.all():
            for vantage in vantages:
                ProviderProbeResult.objects.create(
                    endpoint=endpoint,
                    credential_revision=0,
                    vantage=vantage,
                    state=ProviderProbeStateChoices.HEALTHY,
                    observed_egress='198.51.100.30',
                    checked_at=timezone.now(),
                )
        return promote_inventory(
            inventory.id,
            required_vantages=vantages,
            allow_canary=allow_canary,
        )

    def test_stage_normalizes_but_does_not_activate_inventory(self):
        inventory = self.publish()

        endpoint = inventory.endpoints.get()
        self.assertEqual(inventory.state, ProviderInventoryStateChoices.QUARANTINED)
        self.assertEqual(inventory.revision, 1)
        self.assertEqual(inventory.endpoint_count, 1)
        self.assertEqual(endpoint.host, 'vpn.example')
        self.assertEqual(endpoint.server_name, 'sni.example')

    def test_probed_inventory_can_be_promoted(self):
        staged = self.publish()

        inventory = self.promote(staged, vantages=('bot', 'by'))

        self.assertEqual(inventory.state, ProviderInventoryStateChoices.ACTIVE)
        self.assertIsNotNone(inventory.published_at)

    def test_unprobed_inventory_cannot_be_promoted(self):
        inventory = self.publish()

        with self.assertRaisesMessage(ValidationError, 'probe_incomplete'):
            promote_inventory(inventory.id, required_vantages=('bot',))

        inventory.refresh_from_db()
        self.assertEqual(inventory.state, ProviderInventoryStateChoices.QUARANTINED)

    def test_shared_provider_requires_explicit_canary_promotion(self):
        self.provider.credential_mode = ProviderCredentialModeChoices.SHARED
        self.provider.per_user_lifecycle = False
        self.provider.usage_tracking = False
        self.provider.resale_confirmed_at = None
        self.provider.save(
            update_fields=(
                'credential_mode',
                'per_user_lifecycle',
                'usage_tracking',
                'resale_confirmed_at',
            ),
        )
        inventory = self.publish()
        for endpoint in inventory.endpoints.all():
            ProviderProbeResult.objects.create(
                endpoint=endpoint,
                credential_revision=0,
                vantage='bot',
                state=ProviderProbeStateChoices.HEALTHY,
                observed_egress='198.51.100.30',
            )

        with self.assertRaisesMessage(ValidationError, 'admission_incomplete'):
            promote_inventory(inventory.id, required_vantages=('bot',))

        promoted = promote_inventory(
            inventory.id,
            required_vantages=('bot',),
            allow_canary=True,
        )
        self.assertEqual(promoted.state, ProviderInventoryStateChoices.CANARY)
        self.assertIsNone(active_inventory(self.provider.id))

    def test_new_inventory_atomically_supersedes_previous(self):
        previous = self.promote(self.publish())

        staged = self.publish(payload_digest='e' * 64, endpoint=canonical_endpoint('endpoint-2'))
        previous.refresh_from_db()
        self.assertEqual(previous.state, ProviderInventoryStateChoices.ACTIVE)

        current = self.promote(staged)

        previous.refresh_from_db()
        self.assertEqual(previous.state, ProviderInventoryStateChoices.SUPERSEDED)
        self.assertEqual(current.state, ProviderInventoryStateChoices.ACTIVE)
        self.assertEqual(current.revision, 2)
        self.assertEqual(
            ProviderInventoryVersion.objects.filter(
                provider=self.provider,
                state=ProviderInventoryStateChoices.ACTIVE,
            ).count(),
            1,
        )

    def test_invalid_replacement_keeps_current_inventory(self):
        previous = self.promote(self.publish())
        invalid = canonical_endpoint('invalid')
        invalid = CanonicalEndpoint(**{**invalid.__dict__, 'host': 'bad host'})

        with self.assertRaises(ValidationError):
            self.publish(endpoint=invalid, payload_digest='e' * 64)

        previous.refresh_from_db()
        self.assertEqual(previous.state, ProviderInventoryStateChoices.ACTIVE)
        self.assertEqual(ProviderInventoryVersion.objects.count(), 1)

    def test_empty_inventory_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'empty_inventory'):
            self.publish(endpoints=())

        self.assertFalse(ProviderInventoryVersion.objects.exists())

    def test_duplicate_endpoint_ids_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'duplicate_external_id'):
            self.publish(endpoints=(canonical_endpoint(), canonical_endpoint()))

        self.assertFalse(ProviderInventoryVersion.objects.exists())

    def test_active_inventory_excludes_disabled_provider(self):
        self.promote(self.publish())
        Provider.objects.filter(pk=self.provider.pk).update(enabled=False)

        self.assertIsNone(active_inventory(self.provider.id))

    def test_literal_private_endpoint_is_rejected(self):
        private_endpoint = canonical_endpoint('private')
        private_endpoint = CanonicalEndpoint(**{**private_endpoint.__dict__, 'host': '127.0.0.1'})

        with self.assertRaisesMessage(ValidationError, 'host'):
            self.publish(endpoint=private_endpoint)

    def test_unapproved_public_host_is_rejected(self):
        unexpected = canonical_endpoint('unexpected')
        unexpected = CanonicalEndpoint(**{**unexpected.__dict__, 'host': 'other.example'})

        with self.assertRaisesMessage(ValidationError, 'host_not_allowed'):
            self.publish(endpoint=unexpected)


class CompiledSnapshotTests(ProviderDatabaseTestCase):
    def publish(self, suffix='1'):
        storage_key = f'snapshots/{self.subscription.id}/uri/{suffix}'
        store = FakeSnapshotStore({
            storage_key: SnapshotArtifact(content_digest=CONTENT_DIGEST, size=128),
        })
        return publish_snapshot(
            subscription_id=self.subscription.id,
            client_family='uri',
            storage_key=storage_key,
            content_digest=CONTENT_DIGEST,
            inventory_digest=INVENTORY_DIGEST,
            expires_at=timezone.now() + timedelta(hours=1),
            store=store,
        )

    def test_publish_supersedes_previous_snapshot(self):
        previous = self.publish('1')

        current = self.publish('2')

        previous.refresh_from_db()
        self.assertEqual(previous.state, CompiledSnapshotStateChoices.SUPERSEDED)
        self.assertEqual(current.state, CompiledSnapshotStateChoices.ACTIVE)
        self.assertEqual(current.revision, 2)

    def test_disabled_subscription_cannot_publish(self):
        UserVPN.objects.filter(pk=self.subscription.pk).update(enabled=False)

        with self.assertRaisesMessage(ValidationError, 'disabled'):
            self.publish()

        self.assertFalse(CompiledSnapshot.objects.exists())

    def test_revoke_removes_active_snapshot(self):
        self.publish()

        updated = revoke_snapshots(self.subscription.id)

        self.assertEqual(updated, 1)
        self.assertFalse(
            CompiledSnapshot.objects.filter(state=CompiledSnapshotStateChoices.ACTIVE).exists(),
        )

    def test_missing_artifact_cannot_become_active(self):
        with self.assertRaisesMessage(ValidationError, 'artifact_mismatch'):
            publish_snapshot(
                subscription_id=self.subscription.id,
                client_family='uri',
                storage_key='snapshots/missing',
                content_digest=CONTENT_DIGEST,
                inventory_digest=INVENTORY_DIGEST,
                expires_at=timezone.now() + timedelta(hours=1),
                store=FakeSnapshotStore({}),
            )

        self.assertFalse(CompiledSnapshot.objects.exists())


class ProviderTaskTests(ProviderDatabaseTestCase):
    def tearDown(self):
        unregister_adapter('test-api')

    def test_refresh_task_stages_only_normalized_inventory(self):
        provider = self.create_provider()

        class FakeAdapter(ProviderAdapter):
            @property
            def capabilities(self):
                return ProviderCapabilities(
                    credential_mode='per_user',
                    protocols=('vless',),
                    supports_create=True,
                    supports_suspend=True,
                    supports_resume=True,
                    supports_rotate=True,
                    supports_revoke=True,
                    supports_usage=True,
                )

            def fetch_inventory(self):
                return ProviderInventory(
                    source_digest=SOURCE_DIGEST,
                    payload_digest=PAYLOAD_DIGEST,
                    endpoints=(canonical_endpoint(),),
                    fetched_at=timezone.now(),
                )

        register_adapter('test-api', FakeAdapter)

        result = refresh_provider_inventory.run(provider.id)

        self.assertEqual(result['provider_id'], provider.id)
        self.assertEqual(result['endpoint_count'], 1)
        self.assertEqual(
            ProviderInventoryVersion.objects.get().state,
            ProviderInventoryStateChoices.QUARANTINED,
        )

    def test_refresh_task_does_not_expose_unhandled_adapter_error(self):
        provider = self.create_provider()

        class BrokenAdapter(ProviderAdapter):
            @property
            def capabilities(self):
                return ProviderCapabilities(
                    credential_mode='per_user',
                    protocols=('vless',),
                    supports_create=True,
                    supports_suspend=True,
                    supports_resume=True,
                    supports_rotate=True,
                    supports_revoke=True,
                    supports_usage=True,
                )

            def fetch_inventory(self):
                raise RuntimeError('https://provider.example/sub/secret-bearer')

        register_adapter('test-api', BrokenAdapter)

        with self.assertRaisesMessage(ProviderConfigurationError, 'provider_adapter_failure') as raised:
            refresh_provider_inventory.run(provider.id)

        self.assertNotIn('secret-bearer', str(raised.exception))
