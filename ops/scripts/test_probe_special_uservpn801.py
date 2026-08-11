#!/usr/bin/env python3
"""Focused offline safety tests for the UserVPN 801 canary operator."""
from __future__ import annotations

import importlib.util
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPT = Path(__file__).with_name('probe_special_uservpn801.py')
SPEC = importlib.util.spec_from_file_location('probe_special_uservpn801', SCRIPT)
assert SPEC and SPEC.loader
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


class SingBoxMappingTests(unittest.TestCase):
    def setUp(self):
        reality = {
            'realitySettings': {
                'settings': {'publicKey': 'test-key', 'fingerprint': 'chrome'},
                'serverNames': ['test-name'],
                'shortIds': ['test-short-id'],
            }
        }
        self.bundle = {
            'uuid': '00000000-0000-0000-0000-000000000000',
            'inbounds': {
                '5': {'port': 8443, 'network': 'tcp'},
                '7': {'port': 39329, 'network': 'tcp'},
                '10': {'port': 8080, 'network': 'grpc'},
                '11': {'port': 22554, 'network': 'ws'},
            },
            'probe_streams': {
                '5': reality,
                '7': reality,
                '10': {**reality, 'grpcSettings': {'serviceName': 'test-service', 'multiMode': True}},
                '11': {'wsSettings': {'path': '/test', 'headers': {'Host': 'test-host'}}},
            },
        }

    def test_tcp_reality_is_no_flow_and_uses_target_port(self):
        outbound = operator.singbox_config(self.bundle, 7, 10001)['outbounds'][0]
        self.assertEqual(outbound['server_port'], 39329)
        self.assertEqual(outbound['flow'], '')
        self.assertEqual(outbound['transport'] if 'transport' in outbound else None, None)
        self.assertTrue(outbound['tls']['reality']['enabled'])

    def test_grpc_public_and_diagnostic_endpoints_are_distinct(self):
        public = operator.singbox_config(self.bundle, 10, 10002)['outbounds'][0]
        diagnostic = operator.singbox_config(self.bundle, 10, 10003, diagnostic=True)['outbounds'][0]
        self.assertEqual(public['server_port'], 80)
        self.assertEqual(diagnostic['server_port'], 8080)
        self.assertEqual(public['transport']['type'], 'grpc')

    def test_ws_none_has_no_tls_or_reality(self):
        outbound = operator.singbox_config(self.bundle, 11, 10004)['outbounds'][0]
        self.assertEqual(outbound['transport']['type'], 'ws')
        self.assertNotIn('tls', outbound)
        self.assertEqual(outbound['flow'], '')

    def test_unsupported_tcp_header_fails_closed(self):
        self.bundle['probe_streams']['7']['tcpSettings'] = {'header': {'type': 'http'}}
        with self.assertRaises(operator.GateError):
            operator.singbox_config(self.bundle, 7, 10005)


    def test_grpc_multimode_is_empirical_not_rejected(self):
        outbound = operator.singbox_config(self.bundle, 10, 10006)['outbounds'][0]
        self.assertEqual(outbound['transport']['service_name'], 'test-service')
        self.assertEqual(outbound['server_port'], 80)

    def test_missing_separate_probe_stream_fails_closed(self):
        del self.bundle['probe_streams']['11']
        with self.assertRaises(operator.GateError):
            operator.singbox_config(self.bundle, 11, 10007)


class MutationRecoverySafetyTests(unittest.TestCase):
    def _bundle(self):
        before = {'clients': [], 'port': 1}
        desired = {'id': 'synthetic'}
        return {'fingerprint': 'before', 'inbounds': {'7': before}}, before, desired

    def test_pending_add_exact_after_scoped_deletes(self):
        bundle, before, desired = self._bundle()
        journal = {'operation_id': 'op-20260811T180315Z-533cbef3', 'targets': [],
                   'pending_mutation': {'target': 7, 'action': 'add', 'before': before,
                                        'before_digest': operator.json_digest(before), 'desired': desired,
                                        'desired_digest': operator.json_digest(desired)}}
        recovered = {'fingerprint': 'after', 'inbounds': {'7': {'clients': [desired], 'port': 1}}}
        with patch.object(operator, 'bot_action', return_value=recovered), \
             patch.object(operator, 'scoped_delete', return_value=('clean', {'7': before})), \
             patch.object(operator, 'update_journal'):
            result, retained = operator.recover_pending_mutation(bundle, journal, '/protected')
        self.assertEqual(result['fingerprint'], 'clean')
        self.assertEqual(retained, [])

    def test_ambiguous_or_failed_stable_reread_requires_manual_recovery(self):
        bundle, before, desired = self._bundle()
        journal = {'operation_id': 'op-20260811T180315Z-533cbef3', 'targets': [],
                   'pending_mutation': {'target': 7, 'action': 'add', 'before': before,
                                        'before_digest': operator.json_digest(before), 'desired': desired,
                                        'desired_digest': operator.json_digest(desired)}}
        with patch.object(operator, 'bot_action', return_value={'fingerprint': 'x', 'inbounds': {'7': {'clients': [{'id': 'other'}], 'port': 1}}}):
            with self.assertRaises(operator.GateError):
                operator.recover_pending_mutation(bundle, journal, '/protected')

    def test_bot_timeout_is_a_gate_error(self):
        with patch.object(operator.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 1)):
            with self.assertRaises(operator.GateError):
                operator.bot_action('recover', private=True)

    def test_completed_delete_is_accepted_without_second_delete(self):
        bundle, rollback_before, desired = self._bundle()
        before = {'clients': [desired], 'port': 1}
        journal = {'operation_id': 'op-20260811T180315Z-533cbef3', 'targets': [],
                   'pending_mutation': {'target': 7, 'action': 'delete', 'before': before,
                                        'before_digest': operator.json_digest(before),
                                        'rollback_before': rollback_before, 'desired': desired,
                                        'desired_digest': operator.json_digest(desired)}}
        with patch.object(operator, 'bot_action', return_value={'fingerprint': 'after', 'inbounds': {'7': rollback_before}}), \
             patch.object(operator, 'scoped_delete') as delete, patch.object(operator, 'update_journal'):
            result, retained = operator.recover_pending_mutation(bundle, journal, '/protected')
        delete.assert_not_called()
        self.assertEqual(result['fingerprint'], 'after')
        self.assertEqual(retained, [])


class StaticSafetyTests(unittest.TestCase):
    def test_static_safety_contract(self):
        operator.validate_static()

    def test_tls_check_uses_read_only_export(self):
        with patch.object(operator, 'bot_action', return_value={'ok': True}) as action, \
             patch.object(operator.sys, 'argv', ['operator', '--tls-check']):
            self.assertEqual(operator.main(), 0)
        action.assert_called_once_with('export', private=True)


if __name__ == '__main__':
    unittest.main()
