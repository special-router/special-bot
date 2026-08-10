#!/usr/bin/env bash
set -euo pipefail

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
EXPECTED_COMMIT=${SPECIAL_REDIS_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
APP_COMPOSE=${SPECIAL_BOT_COMPOSE_FILE:-docker-compose.deploy.yml}
OWNER_COMPOSE=${SPECIAL_REDIS_OWNER_COMPOSE_FILE:-/root/vpn_bot/docker-compose.yml}

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
[[ -z "${SPECIAL_BOT_SSH_JUMP:-}" ]] || SSH+=(-J "$SPECIAL_BOT_SSH_JUMP")
SSH+=("root@$BOT_HOST")

"${SSH[@]}" bash -s -- "$REMOTE_PATH" "$APP_COMPOSE" "$OWNER_COMPOSE" "$EXPECTED_COMMIT" <<'REMOTE'
set -euo pipefail
remote_path=$1
app_compose=$2
owner_compose=$3
expected_commit=$4
cd "$remote_path"

unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9-]+$/ {print}')
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected checkout paths'; exit 20; }
[[ $(git rev-parse --short HEAD) == "$expected_commit" ]] || { echo 'BLOCK: unexpected production commit'; exit 21; }
[[ -f .environment && $(stat -c '%a' .environment) == 600 ]] || { echo 'BLOCK: .environment mode'; exit 22; }
[[ -f "$owner_compose" ]] || { echo 'BLOCK: owning Redis Compose file missing'; exit 23; }
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1) == true ]] || { echo 'BLOCK: PostgreSQL not running'; exit 24; }
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-redis-1) == true ]] || { echo 'BLOCK: Redis not running'; exit 25; }
postgres_started=$(docker inspect -f '{{.State.StartedAt}}' vpn_bot-postgres-1)
redis_image_before=$(docker inspect -f '{{.Image}}' vpn_bot-redis-1)

timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
load1=$(awk '{print $1}' /proc/loadavg)
cpus=$(nproc)
mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
blocked=$(ps -eo stat= | awk '$1 ~ /^D/ {n++} END {print n+0}')
awk -v load_value="$load1" -v cpus="$cpus" 'BEGIN {exit !(load_value <= cpus * 4)}' || { echo 'BLOCK: excessive host load'; exit 26; }
(( mem_kb >= 131072 )) || { echo 'BLOCK: insufficient available memory'; exit 27; }
(( blocked == 0 )) || { echo 'BLOCK: D-state tasks present'; exit 28; }
timeout 15 docker info --format '{{.ServerVersion}}' >/dev/null || { echo 'BLOCK: Docker API unavailable'; exit 29; }

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=".environment.bak.$stamp"
new_secret=$(openssl rand -hex 32)
cp --preserve=mode .environment "$backup"
chmod 600 "$backup"

rollback() {
  rc=$?
  trap - EXIT
  if [[ $rc -ne 0 ]]; then
    cp --preserve=mode "$backup" .environment 2>/dev/null || true
    set -a; source .environment; set +a
    export REDIS_PASSWORD
    docker compose -f "$owner_compose" up -d --no-deps --force-recreate redis >/dev/null 2>&1 || true
    docker compose -f "$app_compose" up -d --no-deps web celery celery_beat monitoring >/dev/null 2>&1 || true
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  unset new_secret
  exit "$rc"
}
trap rollback EXIT

python3 - .environment "$new_secret" <<'PY'
import os, sys
from urllib.parse import quote
path, secret = sys.argv[1:]
lines = open(path, encoding='utf-8').read().splitlines()
values = {}
for line in lines:
    if line and not line.lstrip().startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key] = value
host = values.get('REDIS_HOST', 'redis') or 'redis'
port = values.get('REDIS_PORT', '6379') or '6379'
db = values.get('REDIS_DB', '0') or '0'
replacement = {
    'REDIS_PASSWORD': secret,
    'REDIS_URL': f'redis://:{quote(secret, safe="")}@{host}:{port}/{db}',
}
seen = set()
out = []
for line in lines:
    key = line.split('=', 1)[0] if '=' in line else ''
    if key in replacement:
        out.append(f'{key}={replacement[key]}')
        seen.add(key)
    else:
        out.append(line)
for key, value in replacement.items():
    if key not in seen:
        out.append(f'{key}={value}')
fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as handle:
    handle.write('\n'.join(out) + '\n')
PY
chmod 600 .environment
unset new_secret

# Stop only application processes. PostgreSQL remains untouched throughout.
docker compose -f "$app_compose" stop web celery celery_beat monitoring >/dev/null
set -a; source .environment; set +a
export REDIS_PASSWORD
docker compose -f "$owner_compose" up -d --no-deps --force-recreate redis >/dev/null
for _ in $(seq 1 30); do
  if docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" vpn_bot-redis-1 redis-cli ping 2>/dev/null | grep -qx PONG; then
    break
  fi
  sleep 1
done
docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" vpn_bot-redis-1 redis-cli ping 2>/dev/null | grep -qx PONG
docker compose -f "$app_compose" up -d --no-deps web celery celery_beat monitoring >/dev/null
for _ in $(seq 1 30); do
  if docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/tmp/special-redis-audit.out 2>&1; then break; fi
  sleep 2
done
tail -1 /tmp/special-redis-audit.out | grep -qx 'Legacy VPN audit passed.'
rm -f /tmp/special-redis-audit.out
docker exec special-bot-web-1 python manage.py audit_special_monitoring >/dev/null
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1) == true ]]
[[ $(docker inspect -f '{{.State.StartedAt}}' vpn_bot-postgres-1) == "$postgres_started" ]]
[[ $(docker inspect -f '{{.Image}}' vpn_bot-redis-1) == "$redis_image_before" ]]
trap - EXIT
unset REDIS_PASSWORD REDIS_URL
printf 'redis_rotation=passed backup=%s\n' "$backup"
REMOTE
