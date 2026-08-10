import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.monitoring.models import MonitorState, MonitorTransition
from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


class ScaleReadinessCommandTests(TestCase):
    def setUp(self):
        tariff = TariffServer.objects.create(name='daily', price=10)
        self.server = Server.objects.create(
            name='origin', ip_address='192.0.2.1', ssh_username='operator', ssh_password='',
            vpn_username='', vpn_password='', vpn_key='', vpn_url='', client_vpn_host='',
            tariff=tariff, inbound_id=5,
        )

    def user_vpn(self, *, telegram_id: int, sub_id: str, balance: int) -> UserVPN:
        user = TelegramUser.objects.create(telegram_id=telegram_id)
        if balance:
            Transaction.objects.create(
                user=user,
                amount=balance,
                status=TransactionStatusChoices.SUCCESS,
                source=TransactionSourceChoices.YOUMONEY,
            )
        return UserVPN.objects.create(user=user, server=self.server, sub_id=sub_id)

    @override_settings(
        SPECIAL_MONITOR_PAGING_ENABLED=False,
        SPECIAL_MONITOR_PAGING_WEBHOOK_URL='',
        SPECIAL_MONITOR_PAGING_OWNER='',
    )
    def test_report_never_infers_compatibility_or_legacy_retirement(self):
        for layer in ('l0', 'l1', 'l2', 'host'):
            MonitorState.objects.create(layer=layer, last_ok=True, alert=False)
        output = StringIO()

        call_command('validate_scale_readiness', '--json', stdout=output)
        report = json.loads(output.getvalue())

        self.assertTrue(report['monitoring_complete'])
        self.assertFalse(report['paging_configured'])
        self.assertEqual(report['compatibility_ownership'], 'external_private_registry_required')
        self.assertFalse(report['legacy_retirement_ready'])

    def test_non_entitled_sub_id_cannot_mask_entitled_gap(self):
        self.user_vpn(telegram_id=1, sub_id='', balance=10)
        self.user_vpn(telegram_id=2, sub_id='non-entitled', balance=0)
        output = StringIO()

        call_command('validate_scale_readiness', '--json', stdout=output)
        report = json.loads(output.getvalue())

        self.assertEqual(report['entitled_missing_sub_id'], 1)
        self.assertFalse(report['subscription_coverage_complete'])

    def test_duplicate_sub_id_fails_coverage(self):
        self.user_vpn(telegram_id=3, sub_id='duplicate', balance=10)
        self.user_vpn(telegram_id=4, sub_id='duplicate', balance=10)
        output = StringIO()

        call_command('validate_scale_readiness', '--json', stdout=output)
        report = json.loads(output.getvalue())

        self.assertEqual(report['duplicate_sub_ids'], 1)
        self.assertFalse(report['subscription_coverage_complete'])

    def test_stale_monitoring_is_not_complete(self):
        state = MonitorState.objects.create(layer='l1', last_ok=True, alert=False)
        MonitorState.objects.filter(pk=state.pk).update(checked_at=timezone.now() - timedelta(hours=1))
        output = StringIO()

        call_command('validate_scale_readiness', '--json', stdout=output)
        report = json.loads(output.getvalue())

        self.assertNotIn('l1', report['healthy_monitor_layers'])
        self.assertFalse(report['monitoring_complete'])

    @override_settings(
        SPECIAL_MONITOR_PAGING_ENABLED=True,
        SPECIAL_MONITOR_PAGING_WEBHOOK_URL='https://paging.example.invalid/hook',
        SPECIAL_MONITOR_PAGING_OWNER='primary-on-call',
    )
    def test_paging_distinguishes_configuration_from_delivery(self):
        output = StringIO()
        call_command('validate_scale_readiness', '--json', stdout=output)
        configured = json.loads(output.getvalue())
        self.assertTrue(configured['paging_configured'])
        self.assertFalse(configured['paging_delivery_verified'])

        MonitorTransition.objects.create(
            layer='l0', event='opened', notification_delivered=True,
            notification_attempted_at=timezone.now(), notification_destination_owner='primary-on-call',
        )
        output = StringIO()
        call_command('validate_scale_readiness', '--json', stdout=output)
        delivered = json.loads(output.getvalue())
        self.assertTrue(delivered['paging_delivery_verified'])

    def test_origin_metadata_never_claims_live_redundancy(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'origins.json'
            path.write_text(
                json.dumps(
                    [
                        {
                            'id': 'primary', 'enabled': True, 'provider': 'a', 'asn': 64500,
                            'region': 'a', 'public_host': 'a.example.invalid',
                            'health_url': 'https://a.example.invalid/health',
                            'transport': 'vless-reality-tcp', 'priority': 10,
                            'rollout_state': 'production', 'role': 'primary',
                        },
                        {
                            'id': 'secondary', 'enabled': True, 'provider': 'b', 'asn': 64501,
                            'region': 'b', 'public_host': 'b.example.invalid',
                            'health_url': 'https://b.example.invalid/health',
                            'transport': 'vless-reality-tcp', 'priority': 20,
                            'rollout_state': 'pilot', 'role': 'secondary',
                        },
                    ]
                )
            )
            output = StringIO()
            call_command('validate_scale_readiness', '--json', '--origins-file', path, stdout=output)
            report = json.loads(output.getvalue())

        self.assertEqual(report['enabled_origins'], 2)
        self.assertTrue(report['independent_origins_configured'])
        self.assertFalse(report['redundancy_ready'])
