#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
EXPECTED_COMMIT=${SPECIAL_SUBSCRIPTION_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
special_require_commit "$EXPECTED_COMMIT" SPECIAL_EXPECTED_COMMIT
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
special_ssh_require_abs_path "$REMOTE_PATH" SPECIAL_BOT_REMOTE_PATH
COMPOSE_FILE=${SPECIAL_BOT_COMPOSE_FILE:-docker-compose.deploy.yml}
special_ssh_require_relative_path "$COMPOSE_FILE" SPECIAL_BOT_COMPOSE_FILE

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

"${SSH[@]}" sudo -n bash -s -- "$REMOTE_PATH" "$COMPOSE_FILE" "$EXPECTED_COMMIT" <<'REMOTE'
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
      docker compose -f "$compose_file" stop broadcast >/dev/null 2>&1 || true
      docker compose -f "$compose_file" rm -sf broadcast >/dev/null 2>&1 || true
      docker run --rm --network vpn_bot_default --env-file .environment redis:7 \
        sh -c 'redis-cli -u "$REDIS_URL" DEL safe_broadcast_v1 >/dev/null' || true
      echo "BROADCAST_QUARANTINED: rollback removed broadcast worker and purged safe_broadcast_v1 only; generic celery queue untouched" >&2
      # The restored image predates the safe task.  Bring up exactly one web
      # process first, wait for it, then restore non-broadcast workers so its
      # legacy entrypoint can never race migration ownership.
      RUN_MIGRATIONS=false docker compose -f "$compose_file" up -d --no-deps --force-recreate web >/dev/null 2>&1 || true
      for _ in $(seq 1 30); do
        docker exec special-bot-web-1 python manage.py check >/dev/null 2>&1 && break
        sleep 2
      done
      RUN_MIGRATIONS=false docker compose -f "$compose_file" up -d --no-deps --force-recreate celery celery_beat monitoring >/dev/null 2>&1 || true
    fi
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  rm -f "$backup"
  [[ -z "$old_image_id" ]] || docker image rm "$previous_image" >/dev/null 2>&1 || true
  exit "$rc"
}
trap rollback EXIT

timeout 90 docker exec special-bot-web-1 python -c '
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bot.settings")
django.setup()
from apps.monitoring.probes import run_control_plane_probe
result = run_control_plane_probe()
assert result.ok, result.error_class
print("Active control-plane audit passed.")
' >/tmp/special-pre-audit.out 2>&1
pre_audit=$(tail -1 /tmp/special-pre-audit.out)
rm -f /tmp/special-pre-audit.out
[[ "$pre_audit" == 'Active control-plane audit passed.' ]] || { echo 'BLOCK: pre-deploy active control-plane audit failed'; exit 23; }

# Update the existing key only; never print the environment file.
# Delivery flag is left as-is; it will be enabled explicitly after canary validation.
chmod 600 .environment

docker build -t vpnbot:latest .
# This one-shot command is the sole migration owner; all long-running services
# have RUN_MIGRATIONS=false in Compose.
#
# -T and </dev/null are load-bearing, not tidiness. This whole block is fed to
# `bash -s` through a heredoc, so the script itself lives on stdin. Without -T,
# `docker compose run` attaches to that stdin and consumes what is left of it:
# bash then has nothing more to read, every line below this one is silently
# skipped, and the deploy exits 0 having built an image and migrated without
# ever recreating a single container. It reports success and changes nothing.
# Observed 2026-08-13 — the host was on the new commit, the containers on the
# old image, and no error anywhere.
docker compose -f "$compose_file" run --rm -T -e RUN_MIGRATIONS=false web python manage.py migrate --noinput </dev/null
docker compose -f "$compose_file" up -d --no-deps --force-recreate web celery celery_beat monitoring
if docker run --rm --network vpn_bot_default --env-file .environment \
  -e DJANGO_SETTINGS_MODULE=bot.settings vpnbot:latest python -c 'import django; django.setup(); from apps.telegram_bot.tasks import safe_broadcast_v1; assert safe_broadcast_v1.name == "apps.telegram_bot.tasks.safe_broadcast_v1"' >/dev/null; then
  docker compose -f "$compose_file" up -d --no-deps --force-recreate broadcast
else
  docker compose -f "$compose_file" stop broadcast >/dev/null 2>&1 || true
  docker compose -f "$compose_file" rm -sf broadcast >/dev/null 2>&1 || true
  echo 'BROADCAST_QUARANTINED: deployed image lacks safe_broadcast_v1; worker left stopped' >&2
fi

timeout 90 docker exec special-bot-web-1 python -c '
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bot.settings")
django.setup()
from apps.monitoring.probes import run_control_plane_probe
result = run_control_plane_probe()
assert result.ok, result.error_class
print("Active control-plane audit passed.")
' >/tmp/special-post-audit.out 2>&1
post_audit=$(tail -1 /tmp/special-post-audit.out)
rm -f /tmp/special-post-audit.out
[[ "$post_audit" == 'Active control-plane audit passed.' ]] || { echo 'FAIL: post-deploy active control-plane audit failed'; exit 24; }

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
