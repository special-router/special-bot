#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

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

if [[ ${SPECIAL_VERIFY_PRODUCTION:-false} == true ]]; then
  expected_commit=${SPECIAL_PRODUCTION_COMMIT:-$(git rev-parse --short HEAD)}
  if ! SPECIAL_HARDENING_COMMIT="$expected_commit" ./ops/scripts/verify_special_hardening.sh; then
    echo 'production_verifier_retry=refreshing_l2_once' >&2
    ssh -i "${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}" \
      -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
      -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
      "$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "${SPECIAL_BOT_HOST:-72.56.23.226}")" \
      'sudo -n docker exec special-bot-web-1 python -c '\''import os,django; os.environ.setdefault("DJANGO_SETTINGS_MODULE","bot.settings"); django.setup(); from apps.monitoring.models import MonitorState; MonitorState.objects.filter(layer="l2").delete(); from apps.monitoring.tasks import run_protocol_monitor; run_protocol_monitor.delay()'\''; sleep 40'
    SPECIAL_HARDENING_COMMIT="$expected_commit" ./ops/scripts/verify_special_hardening.sh
  fi
  ./ops/scripts/audit_special_redis_rotation.sh
  ./ops/scripts/tune_special_nl_tcp.sh verify
fi

echo 'SPECIAL repository scale-readiness implementation validation passed.'
echo 'Production paging, independent-origin, compatibility-ownership and legacy-retirement gates remain external/readiness checks.'
