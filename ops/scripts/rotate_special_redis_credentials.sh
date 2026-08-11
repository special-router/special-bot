#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
EXPECTED_COMMIT=${SPECIAL_REDIS_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
special_require_commit "$EXPECTED_COMMIT" SPECIAL_EXPECTED_COMMIT
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
special_ssh_require_abs_path "$REMOTE_PATH" SPECIAL_BOT_REMOTE_PATH
APP_COMPOSE=${SPECIAL_BOT_COMPOSE_FILE:-docker-compose.deploy.yml}
special_ssh_require_relative_path "$APP_COMPOSE" SPECIAL_BOT_COMPOSE_FILE
OWNER_COMPOSE=${SPECIAL_REDIS_OWNER_COMPOSE_FILE:-$REMOTE_PATH/docker-compose.infrastructure.yml}
OWNER_ENV=${SPECIAL_REDIS_OWNER_ENV_FILE:-$REMOTE_PATH/.environment}
special_ssh_require_abs_path "$OWNER_COMPOSE" SPECIAL_REDIS_OWNER_COMPOSE_FILE
special_ssh_require_abs_path "$OWNER_ENV" SPECIAL_REDIS_OWNER_ENV_FILE

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
SSH+=("$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "$BOT_HOST")")

"${SSH[@]}" sudo -n bash -s -- "$REMOTE_PATH" "$APP_COMPOSE" "$OWNER_COMPOSE" "$OWNER_ENV" "$EXPECTED_COMMIT" <<'REMOTE'
set -euo pipefail
remote_path=$1
app_compose=$2
owner_compose=$3
owner_env=$4
expected_commit=$5
cd "$remote_path"

unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9A-Za-zT_-]+$/ {print}')
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected checkout paths'; exit 20; }
[[ $(git rev-parse --short HEAD) == "$expected_commit" ]] || { echo 'BLOCK: unexpected production commit'; exit 21; }
[[ -f .environment && $(stat -c '%a' .environment) == 600 ]] || { echo 'BLOCK: .environment mode'; exit 22; }
[[ -f "$owner_compose" ]] || { echo 'BLOCK: owning Redis Compose file missing'; exit 23; }
[[ -f "$owner_env" && $(stat -c '%a' "$owner_env") == 600 ]] || { echo 'BLOCK: owning Redis environment mode'; exit 30; }
# Refuse historical owner Compose files that hardcode requirepass; rotating only
# environment values would silently recreate Redis with the old literal.
if grep -E -- '--requirepass' "$owner_compose" | grep -Evq 'REDIS_PASSWORD'; then
  echo 'BLOCK: owning Redis Compose hardcodes requirepass; migrate it to REDIS_PASSWORD first'
  exit 32
fi
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
owner_backup="$owner_env.bak.$stamp"
new_secret=$(openssl rand -hex 32)
cp --preserve=mode .environment "$backup"
cp --preserve=mode "$owner_env" "$owner_backup"
chmod 600 "$backup" "$owner_backup"

rollback() {
  rc=$?
  trap - EXIT
  if [[ $rc -ne 0 ]]; then
    cp --preserve=mode "$backup" .environment 2>/dev/null || true
    cp --preserve=mode "$owner_backup" "$owner_env" 2>/dev/null || true
    set -a; source "$owner_env"; set +a
    export REDIS_PASSWORD
    docker compose -p vpn_bot --env-file "$owner_env" -f "$owner_compose" up -d --no-deps --force-recreate redis >/dev/null 2>&1 || true
    RUN_MIGRATIONS=false docker compose -f "$app_compose" up -d --no-deps web celery celery_beat monitoring >/dev/null 2>&1 || true
    docker compose -f "$app_compose" stop broadcast >/dev/null 2>&1 || true
    docker compose -f "$app_compose" rm -sf broadcast >/dev/null 2>&1 || true
    docker run --rm --network vpn_bot_default --env-file .environment redis:7 \
      sh -c 'redis-cli -u "$REDIS_URL" DEL safe_broadcast_v1 >/dev/null' || true
    echo "BROADCAST_QUARANTINED: rollback removed broadcast worker and purged safe_broadcast_v1 only; generic celery queue untouched" >&2
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
current_url = values.get('REDIS_URL', '')
if current_url:
    from urllib.parse import urlsplit
    parsed = urlsplit(current_url)
    host = parsed.hostname or values.get('REDIS_HOST', 'redis') or 'redis'
    port = parsed.port or int(values.get('REDIS_PORT', '6379') or '6379')
    db = parsed.path.lstrip('/') or values.get('REDIS_DB', '0') or '0'
else:
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
python3 - "$owner_env" "$new_secret" <<'PY'
import os, sys
path, secret = sys.argv[1:]
lines = open(path, encoding='utf-8').read().splitlines()
seen = False
out = []
for line in lines:
    if line.split('=', 1)[0] == 'REDIS_PASSWORD':
        out.append(f'REDIS_PASSWORD={secret}')
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f'REDIS_PASSWORD={secret}')
fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as handle:
    handle.write('\n'.join(out) + '\n')
PY
chmod 600 .environment "$owner_env"
unset new_secret

# Stop only application processes. PostgreSQL remains untouched throughout.
docker compose -f "$app_compose" stop web celery broadcast celery_beat monitoring >/dev/null
set -a; source "$owner_env"; set +a
export REDIS_PASSWORD
docker compose -p vpn_bot --env-file "$owner_env" -f "$owner_compose" up -d --no-deps --force-recreate redis >/dev/null
for _ in $(seq 1 30); do
  if docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" vpn_bot-redis-1 redis-cli ping 2>/dev/null | grep -qx PONG; then
    break
  fi
  sleep 1
done
docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" vpn_bot-redis-1 redis-cli ping 2>/dev/null | grep -qx PONG
container_secret_hash=$(docker inspect vpn_bot-redis-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | python3 -c 'import hashlib,sys; values=dict(line.rstrip().split("=",1) for line in sys.stdin if "=" in line); print(hashlib.sha256(values.get("REDIS_PASSWORD","").encode()).hexdigest())')
expected_secret_hash=$(python3 -c 'import hashlib,os; print(hashlib.sha256(os.environ["REDIS_PASSWORD"].encode()).hexdigest())')
[[ "$container_secret_hash" == "$expected_secret_hash" ]] || { echo 'FAIL: Redis container secret mismatch'; exit 31; }
RUN_MIGRATIONS=false docker compose -f "$app_compose" up -d --no-deps web celery celery_beat monitoring >/dev/null
if docker run --rm --network vpn_bot_default --env-file .environment \
  -e DJANGO_SETTINGS_MODULE=bot.settings vpnbot:latest python -c 'import django; django.setup(); from apps.telegram_bot.tasks import safe_broadcast_v1; assert safe_broadcast_v1.name == "apps.telegram_bot.tasks.safe_broadcast_v1"' >/dev/null; then
  docker compose -f "$app_compose" up -d --no-deps broadcast >/dev/null
else
  docker compose -f "$app_compose" stop broadcast >/dev/null 2>&1 || true
  docker compose -f "$app_compose" rm -sf broadcast >/dev/null 2>&1 || true
  echo 'BROADCAST_QUARANTINED: image lacks safe_broadcast_v1; worker left stopped' >&2
fi
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
printf 'redis_rotation=passed app_backup=%s owner_backup=%s\n' "$backup" "$owner_backup"
REMOTE
