#!/usr/bin/env python3
"""Validate SPECIAL Bot docs and operator scripts without reading secrets."""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN = [
    ROOT / 'README.md',
    ROOT / 'CLAUDE.md',
    *sorted((ROOT / 'docs').rglob('*.md')),
]
SETTINGS_FILE = ROOT / 'bot' / 'settings.py'
FLAGS_DOC = ROOT / 'docs' / 'FLAGS.md'
REQUIREMENTS = ROOT / 'requirements.txt'
SHELL = sorted((ROOT / 'ops' / 'scripts').glob('*.sh'))
PYTHON_SCRIPTS = sorted((ROOT / 'ops' / 'scripts').glob('*.py'))
CANONICAL_OPERATOR_SCRIPTS = (
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
BOOTSTRAP_ROOT_SCRIPT = 'harden_special_ssh.sh'

STALE_MARKERS = (
    'vpn-ops/scripts',
    '/Projects/vpn-ops',
    'special-router-dev/tmp/docs-main',
    'special-router-dev/tmp/legacy-stabilization',
    'L2 pending canary',
    'monitoring cannot be deployed',
    'Customer-facing subscription delivery remains disabled',
)

# Patterns represent value-bearing material, not ordinary secret-handling prose.
SECRET_PATTERNS = {
    'telegram_token': re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
    'private_key': re.compile(r'-----BEGIN (?:OPENSSH|RSA|EC|PRIVATE) PRIVATE KEY-----'),
    'bearer_url': re.compile(r'https://[^\s)`]+/sub/[A-Za-z0-9_-]{12,}'),
    'vless_uri': re.compile(r'vless://[0-9a-fA-F-]{36}@'),
}
LINK_PATTERN = re.compile(r'\[[^\]]+\]\(([^)]+)\)')

# A setting reaches the runtime only through ``env.<type>('NAME'`` or the
# canary JSON helper; a documented flag is the first cell of a FLAGS.md row.
ENV_SETTING_PATTERN = re.compile(r"(?:env\.\w+|_internal_canary_json)\(\s*'([A-Z][A-Z0-9_]*)'")
FLAG_ROW_PATTERN = re.compile(r'^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|', re.MULTILINE)

# uv writes one unindented ``name==version`` per pin and indents every comment.
PIN_PATTERN = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)', re.MULTILINE)

# Only py3xui is guarded rather than every pin, because it is the sole runtime
# dependency whose internals this repository subclasses (``utils/py3xui/``) and
# whose panel endpoints differ between releases, so a test venv on another
# version silently asserts a request the image never sends.
GUARDED_RUNTIME_DEPENDENCIES = ('py3xui',)


def fail(message: str) -> None:
    print(f'ERROR: {message}', file=sys.stderr)
    raise SystemExit(1)


def check_markdown() -> None:
    for path in MARKDOWN:
        text = path.read_text(encoding='utf-8')
        relative = path.relative_to(ROOT)
        for marker in STALE_MARKERS:
            if marker in text:
                fail(f'{relative}: stale cross-project/current-state marker: {marker!r}')
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                fail(f'{relative}: possible {name}')
        for target in LINK_PATTERN.findall(text):
            if '://' in target or target.startswith(('#', 'mailto:')):
                continue
            destination = path.parent / target.split('#', 1)[0]
            if not destination.exists():
                fail(f'{relative}: broken link {target!r}')


def check_scripts() -> None:
    for path in SHELL:
        text = path.read_text(encoding='utf-8')
        relative = path.relative_to(ROOT)
        for marker in STALE_MARKERS:
            if marker in text:
                fail(f'{relative}: stale cross-project path: {marker!r}')
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                fail(f'{relative}: possible {name}')
        if path.stat().st_mode & 0o111 == 0:
            fail(f'{relative}: script is not executable')
    helper = ROOT / 'ops' / 'scripts' / 'special_ssh.sh'
    if not helper.exists():
        fail('ops/scripts/special_ssh.sh: named-operator helper missing')
    helper_text = helper.read_text(encoding='utf-8')
    if 'SPECIAL_SSH_USER:=specialops' not in helper_text:
        fail('ops/scripts/special_ssh.sh: specialops default missing')
    bootstrap = (ROOT / 'ops' / 'scripts' / BOOTSTRAP_ROOT_SCRIPT).read_text(encoding='utf-8')
    if 'PermitRootLogin no' not in bootstrap or 'SPECIAL_SSH_HARDEN_APPROVED' not in bootstrap:
        fail('ops/scripts/harden_special_ssh.sh: approved root-login cutover gate missing')
    for name in CANONICAL_OPERATOR_SCRIPTS:
        path = ROOT / 'ops' / 'scripts' / name
        text = path.read_text(encoding='utf-8')
        if 'root@' in text:
            fail(f'ops/scripts/{name}: direct root SSH target is forbidden')
        if 'special_ssh.sh' not in text:
            fail(f'ops/scripts/{name}: named-operator helper is required')
        if 'SPECIAL_BOT_SSH_USER' not in text and 'SPECIAL_NL_SSH_USER' not in text:
            fail(f'ops/scripts/{name}: host-specific operator variable is required')
        if 'sudo -n' not in text:
            fail(f'ops/scripts/{name}: non-interactive sudo is required')


def flag_drift(settings_text: str, flags_text: str) -> tuple[list[str], list[str]]:
    """Return settings missing a documented row, and rows naming no setting."""
    configured = set(ENV_SETTING_PATTERN.findall(settings_text))
    documented = set(FLAG_ROW_PATTERN.findall(flags_text))
    return sorted(configured - documented), sorted(documented - configured)


def check_flags() -> int:
    """Documentation of a flag must rot loudly, not silently."""
    settings_text = SETTINGS_FILE.read_text(encoding='utf-8')
    flags_text = FLAGS_DOC.read_text(encoding='utf-8')
    undocumented, unknown = flag_drift(settings_text, flags_text)
    if undocumented:
        fail(f'docs/FLAGS.md: no row for {", ".join(undocumented)}')
    if unknown:
        fail(f'docs/FLAGS.md: {", ".join(unknown)} is documented but absent from bot/settings.py')
    return len(set(ENV_SETTING_PATTERN.findall(settings_text)))


def dependency_drift(requirements_text: str, installed: dict[str, str | None]) -> list[str]:
    """Return one message per guarded dependency this interpreter contradicts."""
    pinned = dict(PIN_PATTERN.findall(requirements_text))
    drift = []
    for name in GUARDED_RUNTIME_DEPENDENCIES:
        expected = pinned.get(name)
        if expected is None:
            drift.append(f'{name} is not pinned in requirements.txt')
            continue
        present = installed.get(name)
        if present is not None and present != expected:
            drift.append(f'{name}: requirements.txt pins {expected}, this interpreter has {present}')
    return drift


def installed_versions() -> dict[str, str | None]:
    """A dependency the interpreter lacks is ``None``: an absence, not a drift."""
    versions: dict[str, str | None] = {}
    for name in GUARDED_RUNTIME_DEPENDENCIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def check_dependency_pins() -> str:
    """The suite must exercise the version the image installs, not a later one."""
    installed = installed_versions()
    drift = dependency_drift(REQUIREMENTS.read_text(encoding='utf-8'), installed)
    if drift:
        fail('; '.join(drift))
    return ','.join(f'{name}={version or "absent"}' for name, version in sorted(installed.items()))


def main() -> None:
    check_markdown()
    check_scripts()
    documented_flags = check_flags()
    pins = check_dependency_pins()
    print(
        f'repository_validation=passed markdown={len(MARKDOWN)} '
        f'shell={len(SHELL)} python_scripts={len(PYTHON_SCRIPTS)} '
        f'flags={documented_flags} pins={pins}'
    )


if __name__ == '__main__':
    main()
