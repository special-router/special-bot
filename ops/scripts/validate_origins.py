#!/usr/bin/env python3
"""Validate non-secret SPECIAL origin metadata before redundancy claims."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ops.origins import validate_origins  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <origins.json>', file=sys.stderr)
        raise SystemExit(2)
    rows = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('origin document must be a list')
    result = validate_origins(rows)
    print(' '.join(f'{key}={str(value).lower()}' for key, value in result.items()))


if __name__ == '__main__':
    main()
