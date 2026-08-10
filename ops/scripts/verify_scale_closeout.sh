#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON=${SPECIAL_VERIFY_PYTHON:-$ROOT/.venv/bin/python}
[[ -x "$PYTHON" ]] || {
  echo "BLOCK: set SPECIAL_VERIFY_PYTHON to the preserved project test venv" >&2
  exit 20
}
cd "$ROOT"

python3 ops/scripts/validate_repository.py
python3 -m unittest ops/scripts/test_validate_origins.py
python3 ops/scripts/validate_origins.py ops/origins.example.json
bash -n entrypoint.sh ops/scripts/*.sh

git diff --check
DJANGO_SETTINGS_MODULE=bot.settings \
DATABASE_URL='sqlite:///:memory:' \
CELERY_ALWAYS_EAGER=true \
  "$PYTHON" manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=bot.settings \
DATABASE_URL='sqlite:///:memory:' \
CELERY_ALWAYS_EAGER=true \
  "$PYTHON" -m pytest -q

echo 'SPECIAL repository scale-readiness implementation validation passed.'
echo 'Production paging, independent-origin, compatibility-ownership and legacy-retirement gates remain external/readiness checks.'
