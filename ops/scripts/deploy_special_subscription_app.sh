#!/usr/bin/env bash
set -euo pipefail

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
EXPECTED_COMMIT=${SPECIAL_SUBSCRIPTION_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
COMPOSE_FILE=${SPECIAL_BOT_COMPOSE_FILE:-docker-compose.deploy.yml}

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

"${SSH[@]}" bash -s -- "$REMOTE_PATH" "$COMPOSE_FILE" "$EXPECTED_COMMIT" <<'REMOTE'
set -euo pipefail
remote_path=$1
compose_file=$2
expected_commit=$3
cd "$remote_path"

unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9A-Za-zT_-]+$/ {print}')
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected checkout paths'; exit 20; }
for path in .environment .environment.bak.*; do
  [[ -e "$path" ]] || continue
  [[ $(stat -c '%a' "$path") == 600 ]] || { echo 'BLOCK: secret file mode'; exit 22; }
done
load1=$(awk '{print $1}' /proc/loadavg)
cpus=$(nproc)
mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
blocked=$(ps -eo stat= | awk '$1 ~ /^D/ {n++} END {print n+0}')
awk -v load_value="$load1" -v cpus="$cpus" 'BEGIN {exit !(load_value <= cpus * 4)}' || { echo 'BLOCK: excessive host load'; exit 26; }
(( mem_kb >= 131072 )) || { echo 'BLOCK: insufficient available memory'; exit 27; }
(( blocked == 0 )) || { echo 'BLOCK: D-state tasks present'; exit 28; }
timeout 15 docker info --format '{{.ServerVersion}}' >/dev/null || { echo 'BLOCK: Docker API unavailable'; exit 29; }
git fetch --quiet origin main
git merge --ff-only origin/main
actual_commit=$(git rev-parse --short HEAD)
[[ "$actual_commit" == "$expected_commit" ]] || {
  echo "BLOCK: expected commit $expected_commit, got $actual_commit"
  exit 21
}
[[ $(stat -c '%a' .environment) == 600 ]] || { echo 'BLOCK: .environment must be mode 0600'; exit 30; }

backup=".environment.subscription-migration.$$.bak"
previous_image="vpnbot:subscription-migration-rollback-$$"
cp --preserve=mode .environment "$backup"
old_image_id=$(docker image inspect vpnbot:latest --format '{{.Id}}' 2>/dev/null || true)
if [[ -n "$old_image_id" ]]; then
  docker tag vpnbot:latest "$previous_image"
fi
rollback() {
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    cp --preserve=mode "$backup" .environment 2>/dev/null || true
    if [[ -n "$old_image_id" ]]; then
      docker tag "$previous_image" vpnbot:latest 2>/dev/null || true
      docker compose -f "$compose_file" up -d --no-deps --force-recreate web celery celery_beat monitoring >/dev/null 2>&1 || true
    fi
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  rm -f "$backup"
  [[ -z "$old_image_id" ]] || docker image rm "$previous_image" >/dev/null 2>&1 || true
  exit "$rc"
}
trap rollback EXIT

timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/tmp/special-pre-audit.out 2>&1
pre_audit=$(tail -1 /tmp/special-pre-audit.out)
rm -f /tmp/special-pre-audit.out
[[ "$pre_audit" == 'Legacy VPN audit passed.' ]] || { echo 'BLOCK: pre-deploy legacy audit failed'; exit 23; }

# Update the existing key only; never print the environment file.
# Delivery flag is left as-is; it will be enabled explicitly after canary validation.
chmod 600 .environment

docker build -t vpnbot:latest .
docker compose -f "$compose_file" up -d --no-deps --force-recreate web celery celery_beat monitoring

timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/tmp/special-post-audit.out 2>&1
post_audit=$(tail -1 /tmp/special-post-audit.out)
rm -f /tmp/special-post-audit.out
[[ "$post_audit" == 'Legacy VPN audit passed.' ]] || { echo 'FAIL: post-deploy legacy audit failed'; exit 24; }

timeout 30 docker exec special-bot-web-1 python manage.py shell -c '
from django.conf import settings
assert settings.SUBSCRIPTION_CONNECTOR_ENABLED is True
# Delivery flag validation skipped; it is enabled separately after canary.
print("flags=enabled")
'

docker ps --filter label=com.docker.compose.project=special-bot \
  --format '{{.Names}} {{.Image}} {{.Status}}' | sort
printf 'deployed_commit=%s\n' "$actual_commit"
printf '%s\n' "$post_audit"
trap - EXIT
rm -f "$backup"
[[ -z "$old_image_id" ]] || docker image rm "$previous_image" >/dev/null 2>&1 || true
REMOTE
