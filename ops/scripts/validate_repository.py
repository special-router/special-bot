#!/usr/bin/env python3
"""Validate SPECIAL Bot docs and operator scripts without reading secrets."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN = [ROOT / 'README.md', *sorted((ROOT / 'docs').glob('*.md'))]
SHELL = sorted((ROOT / 'ops' / 'scripts').glob('*.sh'))
PYTHON_SCRIPTS = sorted((ROOT / 'ops' / 'scripts').glob('*.py'))

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


def main() -> None:
    check_markdown()
    check_scripts()
    print(
        f'repository_validation=passed markdown={len(MARKDOWN)} '
        f'shell={len(SHELL)} python_scripts={len(PYTHON_SCRIPTS)}'
    )


if __name__ == '__main__':
    main()
