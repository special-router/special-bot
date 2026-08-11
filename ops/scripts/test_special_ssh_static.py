#!/usr/bin/env python3
"""Static contract tests for named-operator SPECIAL SSH tooling."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'ops' / 'scripts'
CANONICAL = (
    'preflight_special_subscription.sh',
    'deploy_special_subscription_app.sh',
    'backfill_special_subscription_ids.sh',
    'rotate_special_xui_credentials.sh',
    'rotate_special_redis_credentials.sh',
    'consolidate_special_vless_listener.sh',
    'tune_special_nl_tcp.sh',
    'verify_special_hardening.sh',
    'verify_special_full_backlog.sh',
    'verify_scale_closeout.sh',
    'preflight_special_infrastructure_adoption.sh',
    'adopt_special_infrastructure_ownership.sh',
    'audit_special_redis_rotation.sh',
    'retire_special_legacy_app_assets.sh',
)


class SpecialSshStaticTests(unittest.TestCase):
    def test_canonical_remote_scripts_use_named_operator_and_sudo(self):
        for name in CANONICAL:
            with self.subTest(name=name):
                text = (SCRIPTS / name).read_text(encoding='utf-8')
                self.assertNotIn('root@', text)
                self.assertIn('special_ssh.sh', text)
                self.assertIn('sudo -n', text)
                self.assertTrue(
                    'SPECIAL_BOT_SSH_USER' in text or 'SPECIAL_NL_SSH_USER' in text
                )

    def test_secret_artifact_scp_stages_under_tmp(self):
        for name in ('backfill_special_subscription_ids.sh', 'rotate_special_xui_credentials.sh'):
            with self.subTest(name=name):
                text = (SCRIPTS / name).read_text(encoding='utf-8')
                self.assertIn('special_ssh_require_tmp_dir', text)
                self.assertIn('SPECIAL_SSH_TMP_DIR', text)
                self.assertIn('mktemp -d', text)
                self.assertIn('rm -rf', text)
                self.assertNotIn('REMOTE_IDS="/root/', text)
                self.assertNotIn('BOT_BUNDLE="/root/', text)
                self.assertNotIn('NL_BUNDLE="/root/', text)

    def test_hardening_has_required_fail_closed_gates(self):
        text = (SCRIPTS / 'harden_special_ssh.sh').read_text(encoding='utf-8')
        for required in (
            'SPECIAL_SSH_HARDEN_APPROVED', 'systemd-run', 'PermitRootLogin no',
            'sshd -T -C', 'ControlMaster=no', 'ControlPath=none',
            'sudo -n id -u', 'fresh root SSH remained accepted',
            'SPECIAL_SSH_OPERATOR_PUBLIC_KEY_FILE', 'rollback watchdog already fired',
            'operator public key does not match the private key',
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == '__main__':
    unittest.main()
