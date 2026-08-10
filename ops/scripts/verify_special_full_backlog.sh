#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON=${SPECIAL_VERIFY_PYTHON:-$ROOT/.venv/bin/python}
BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
SSH=(
  ssh -i "$SSH_KEY"
  -o BatchMode=yes
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10
  -o ConnectionAttempts=1
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=2
)
if [[ -n "${SPECIAL_BOT_SSH_JUMP:-}" ]]; then
  SSH+=(-J "$SPECIAL_BOT_SSH_JUMP")
fi
SSH+=("root@$BOT_HOST")

[[ -x "$PYTHON" ]] || {
  echo "BLOCK: Python venv not found at $PYTHON; set SPECIAL_VERIFY_PYTHON" >&2
  exit 20
}

printf '%s\n' '=== local source validation ==='
cd "$ROOT"
git diff --check
unexpected=$(git status --porcelain | grep -v '^?? .pi-subagents/$' || true)
[[ -z "$unexpected" ]] || {
  printf '%s\n' "$unexpected" >&2
  echo 'BLOCK: local checkout has unexpected changes' >&2
  exit 21
}
DJANGO_SETTINGS_MODULE=bot.settings \
DATABASE_URL='sqlite:///:memory:' \
CELERY_ALWAYS_EAGER=true \
  "$PYTHON" -m pytest -q

printf '%s\n' '=== production aggregate validation ==='
timeout "${SPECIAL_VERIFY_REMOTE_TIMEOUT:-180}" "${SSH[@]}" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /root/special-bot
printf 'production_commit='; git rev-parse --short HEAD
printf '%s\n' 'containers:'
docker ps --filter label=com.docker.compose.project=special-bot \
  --format '{{.Names}} {{.Image}} {{.Status}}' | sort
printf '%s\n' 'legacy invariant:'
timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn 2>&1 | tail -2
printf '%s\n' 'monitoring states:'
timeout 30 docker exec special-bot-web-1 python manage.py audit_special_monitoring 2>&1 | tail -3
printf '%s\n' 'xray:'
timeout 20 docker exec special-bot-monitoring-1 /usr/local/bin/xray version 2>&1 | head -1
REMOTE
