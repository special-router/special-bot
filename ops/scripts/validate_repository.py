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

# Every pin is guarded rather than py3xui alone: the check is one dictionary
# comparison either way, and the shared test venv was rebuilt from
# requirements.txt on 2026-08-13, so the only thing it can report now is drift
# that appeared afterwards. Most of pyproject.toml is still unpinned, which is
# what lets a rebuilt venv drift again; this is what makes that loud.
REBUILD_HINT = (
    'rebuild the venv from requirements.txt '
    '(uv pip install --python <venv>/bin/python -r requirements.txt); '
    'never loosen a pin to match an environment'
)


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


def pinned_requirements(requirements_text: str) -> dict[str, str]:
    """Return every ``name==version`` pin, ignoring uv's indented ``# via`` comments."""
    return dict(PIN_PATTERN.findall(requirements_text))


def dependency_drift(pinned: dict[str, str], installed: dict[str, str | None]) -> list[str]:
    """Name only the pins this interpreter contradicts, one line each.

    A dependency the interpreter does not have is an absence, not a drift: the
    validator also runs on a bare interpreter that installs none of them.
    """
    return [
        f'{name}: requirements.txt pins {expected}, this interpreter has {installed[name]}'
        for name, expected in sorted(pinned.items())
        if installed.get(name) is not None and installed[name] != expected
    ]


def installed_versions(names: list[str]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def check_dependency_pins() -> str:
    """The suite must exercise the versions the image installs, not later ones."""
    pinned = pinned_requirements(REQUIREMENTS.read_text(encoding='utf-8'))
    if not pinned:
        fail('requirements.txt: no pins parsed, the dependency guard would check nothing')
    if sys.prefix == sys.base_prefix:
        # A system interpreter carries distro packages nobody pinned — this
        # machine's python3 has 21 of these 44 — and it never runs the suite.
        return f'skipped-outside-venv/{len(pinned)}'
    installed = installed_versions(sorted(pinned))
    drift = dependency_drift(pinned, installed)
    if drift:
        subject = 'dependency does' if len(drift) == 1 else 'dependencies do'
        fail(
            f'{len(drift)} pinned {subject} not match this interpreter:\n  '
            + '\n  '.join(drift)
            + f'\n{REBUILD_HINT}'
        )
    verified = sum(1 for version in installed.values() if version is not None)
    return f'{verified}/{len(pinned)}'


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
