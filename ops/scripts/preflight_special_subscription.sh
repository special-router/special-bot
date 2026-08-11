#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

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
SSH+=("$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "$BOT_HOST")")

timeout "${SPECIAL_PREFLIGHT_TIMEOUT:-240}" "${SSH[@]}" sudo -n bash -s <<'REMOTE'
set -euo pipefail
cd /root/special-bot
printf 'revision='; git rev-parse --short HEAD
unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9A-Za-zT_-]+$/ {print}')
printf 'source_clean='; [[ -z "$unexpected" ]] && echo true || echo false
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected checkout paths'; exit 20; }
secret_files=0
for path in .environment .environment.bak.*; do
  [[ -e "$path" ]] || continue
  [[ $(stat -c '%a' "$path") == 600 ]] || { echo 'BLOCK: secret file mode'; exit 21; }
  secret_files=$((secret_files + 1))
done
printf 'secret_files_0600=%d\n' "$secret_files"
load1=$(awk '{print $1}' /proc/loadavg)
cpus=$(nproc)
mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
blocked=$(ps -eo stat= | awk '$1 ~ /^D/ {n++} END {print n+0}')
printf 'host_health=load1:%s cpus:%s mem_available_kb:%s d_state:%s\n' "$load1" "$cpus" "$mem_kb" "$blocked"
awk -v load_value="$load1" -v cpus="$cpus" 'BEGIN {exit !(load_value <= cpus * 4)}' || { echo 'BLOCK: excessive host load'; exit 22; }
(( mem_kb >= 131072 )) || { echo 'BLOCK: insufficient available memory'; exit 23; }
(( blocked == 0 )) || { echo 'BLOCK: D-state tasks present'; exit 24; }
timeout 15 docker info --format '{{.ServerVersion}}' >/dev/null || { echo 'BLOCK: Docker API unavailable'; exit 25; }
printf '%s\n' 'containers:'
timeout 15 docker ps --filter label=com.docker.compose.project=special-bot \
  --format '{{.Names}} {{.Image}} {{.Status}}' | sort
printf '%s\n' 'legacy_audit:'
timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn 2>&1 | tail -2
printf '%s\n' 'subid_coverage:'
if timeout 30 docker exec special-bot-web-1 python manage.py help audit_xui_sub_id_coverage >/dev/null 2>&1; then
  timeout 90 docker exec special-bot-web-1 python manage.py audit_xui_sub_id_coverage 2>&1 | tail -8
else
  echo 'command=not_deployed'
fi
printf '%s\n' 'monitoring:'
timeout 30 docker exec special-bot-web-1 python manage.py shell -c '
from apps.monitoring.models import MonitorState
for state in MonitorState.objects.order_by("layer"):
    print(state.layer, state.last_ok, state.alert, state.consecutive_failures)
' 2>/dev/null | grep -E '^(l0|l1|l2) '
printf '%s\n' 'flags:'
timeout 30 docker exec special-bot-web-1 python manage.py shell -c '
from django.conf import settings
print("connector", settings.SUBSCRIPTION_CONNECTOR_ENABLED)
print("delivery", settings.SUBSCRIPTION_DELIVERY_ENABLED)
print("monitor", settings.SPECIAL_MONITOR_ENABLED)
print("l2", settings.SPECIAL_MONITOR_L2_ENABLED)
' 2>/dev/null | grep -E '^(connector|delivery|monitor|l2) '
REMOTE
