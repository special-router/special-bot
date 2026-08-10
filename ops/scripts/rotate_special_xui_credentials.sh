#!/usr/bin/env bash
set -euo pipefail

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
NL_HOST=${SPECIAL_XUI_HOST:-195.66.213.74}
SERVER_ID=${SPECIAL_SERVER_ID:-1}
EXPECTED_COMMIT=${SPECIAL_SUBSCRIPTION_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BOT_BUNDLE="/root/.special-xui-rotation.$STAMP.json"
BOT_BACKUP="/root/.special-xui-server.$STAMP.json"
NL_BUNDLE="/root/.special-xui-rotation.$STAMP.json"
NL_BACKUP="/etc/x-ui/x-ui.db.subscription-rotation.$STAMP.bak"
TMP_DIR=$(mktemp -d)
BUNDLE="$TMP_DIR/rotation.json"

SSH_OPTIONS=(
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10
  -o ConnectionAttempts=1
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=2
)
cleanup_local() {
  rm -rf "$TMP_DIR"
}
trap cleanup_local EXIT
chmod 700 "$TMP_DIR"

BOT="root@$BOT_HOST"
NL="root@$NL_HOST"
ssh "${SSH_OPTIONS[@]}" "$BOT" 'echo bot_ssh=ok' >/dev/null
ssh "${SSH_OPTIONS[@]}" "$NL" 'echo nl_ssh=ok' >/dev/null

username="special_$(openssl rand -hex 8)"
password=$(openssl rand -hex 32)
web_path="/$(openssl rand -hex 9)/"
python3 - "$BUNDLE" "$username" "$password" "$web_path" <<'PY'
import json, os, sys
path, username, password, web_path = sys.argv[1:]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as handle:
    json.dump({'username': username, 'password': password, 'web_path': web_path}, handle)
PY
unset username password web_path

scp "${SSH_OPTIONS[@]}" "$BUNDLE" "$BOT:$BOT_BUNDLE" >/dev/null
if ! scp "${SSH_OPTIONS[@]}" "$BUNDLE" "$NL:$NL_BUNDLE" >/dev/null; then
  ssh "${SSH_OPTIONS[@]}" "$BOT" "rm -f -- '$BOT_BUNDLE'" >/dev/null 2>&1 || true
  exit 24
fi

rollback() {
  rc=$?
  trap - EXIT
  if [[ "$rc" -ne 0 ]]; then
    # Restore the panel first so restarted bot services never use credentials
    # that point at the wrong control-plane state.
    ssh "${SSH_OPTIONS[@]}" "$NL" bash -s -- "$NL_BACKUP" <<'NLROLLBACK' >/dev/null 2>&1 || true
set -u
backup=$1
if [[ -f "$backup" ]]; then
  cp --preserve=mode "$backup" /etc/x-ui/x-ui.db
  chmod 600 /etc/x-ui/x-ui.db
  x-ui restart >/dev/null 2>&1 || true
fi
NLROLLBACK
    ssh "${SSH_OPTIONS[@]}" "$BOT" bash -s -- "$BOT_BACKUP" "$SERVER_ID" <<'BOTROLLBACK' >/dev/null 2>&1 || true
set -u
backup=$1
server_id=$2
cd /root/special-bot 2>/dev/null || exit 0
if [[ -f "$backup" ]]; then
  docker run --rm --network vpn_bot_default --env-file .environment \
    -e DJANGO_SETTINGS_MODULE=bot.settings \
    -v "$backup:/tmp/special-xui-server-backup.json:ro" \
    vpnbot:latest python manage.py shell -c '
import json
from apps.servers.models import Server
with open("/tmp/special-xui-server-backup.json") as handle:
    data = json.load(handle)
Server.objects.filter(id=int(data["id"])).update(
    vpn_username=data["vpn_username"],
    vpn_password=data["vpn_password"],
    vpn_url=data["vpn_url"],
)
' >/dev/null 2>&1 || true
fi
RUN_MIGRATIONS=false docker compose -f docker-compose.deploy.yml up -d --no-deps web celery celery_beat monitoring >/dev/null 2>&1 || true
    docker compose -f docker-compose.deploy.yml stop broadcast >/dev/null 2>&1 || true
    docker compose -f docker-compose.deploy.yml rm -sf broadcast >/dev/null 2>&1 || true
    docker run --rm --network vpn_bot_default --env-file .environment redis:7 \
      sh -c 'redis-cli -u "$REDIS_URL" DEL safe_broadcast_v1 >/dev/null' || true
    echo "BROADCAST_QUARANTINED: rollback removed broadcast worker and purged safe_broadcast_v1 only; generic celery queue untouched" >&2
BOTROLLBACK
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  ssh "${SSH_OPTIONS[@]}" "$BOT" "rm -f -- '$BOT_BUNDLE'" >/dev/null 2>&1 || true
  ssh "${SSH_OPTIONS[@]}" "$NL" "rm -f -- '$NL_BUNDLE'" >/dev/null 2>&1 || true
  cleanup_local
  exit "$rc"
}
trap rollback EXIT

ssh "${SSH_OPTIONS[@]}" "$BOT" bash -s -- \
  "$BOT_BUNDLE" "$BOT_BACKUP" "$SERVER_ID" "$EXPECTED_COMMIT" <<'BOTPREP'
set -euo pipefail
bundle=$1
backup=$2
server_id=$3
expected_commit=$4
cd /root/special-bot
unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9A-Za-zT_-]+$/ {print}')
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected BOT checkout paths'; exit 20; }
for path in .environment .environment.bak.* "$bundle"; do
  [[ -e "$path" ]] || continue
  [[ $(stat -c '%a' "$path") == 600 ]] || { echo 'BLOCK: secret file mode'; exit 22; }
done
[[ $(git rev-parse --short HEAD) == "$expected_commit" ]] || { echo 'BLOCK: unexpected BOT commit'; exit 21; }
load1=$(awk '{print $1}' /proc/loadavg)
cpus=$(nproc)
mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
blocked=$(ps -eo stat= | awk '$1 ~ /^D/ {n++} END {print n+0}')
awk -v load_value="$load1" -v cpus="$cpus" 'BEGIN {exit !(load_value <= cpus * 4)}' || { echo 'BLOCK: excessive BOT load'; exit 24; }
(( mem_kb >= 131072 )) || { echo 'BLOCK: insufficient BOT memory'; exit 25; }
(( blocked == 0 )) || { echo 'BLOCK: BOT D-state tasks present'; exit 26; }
timeout 15 docker info --format '{{.ServerVersion}}' >/dev/null || { echo 'BLOCK: BOT Docker API unavailable'; exit 27; }
timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
# Prove the current connector can authenticate before stopping app services.
timeout 60 docker exec special-bot-web-1 python manage.py shell -c '
import asyncio
from apps.servers.models import Server
from utils.py3xui.async_api import AsyncApi
async def check():
    server = await Server.objects.aget(id=1)
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password, use_tls_verify=False)
    await api.login()
    await api.inbound.get_by_id(server.inbound_id)
asyncio.run(check())
' >/dev/null
umask 077
docker exec special-bot-web-1 python manage.py shell -c "
import json
from apps.servers.models import Server
server = Server.objects.get(id=$server_id)
print(json.dumps({
    'id': server.id,
    'vpn_username': server.vpn_username,
    'vpn_password': server.vpn_password,
    'vpn_url': server.vpn_url,
}))
" > "$backup"
chmod 600 "$backup"
docker compose -f docker-compose.deploy.yml stop web celery broadcast celery_beat monitoring >/dev/null
BOTPREP

ssh "${SSH_OPTIONS[@]}" "$NL" bash -s -- "$NL_BUNDLE" "$NL_BACKUP" <<'NLUPDATE'
set -euo pipefail
bundle=$1
backup=$2
[[ $(stat -c '%a' "$bundle") == 600 ]] || { echo 'BLOCK: NL bundle mode'; exit 30; }
[[ $(stat -c '%a' /etc/x-ui/x-ui.db) == 600 ]] || { echo 'BLOCK: x-ui database mode'; exit 31; }
umask 077
sqlite3 /etc/x-ui/x-ui.db ".backup '$backup'"
chmod 600 "$backup"
username=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["username"])' "$bundle")
password=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["password"])' "$bundle")
web_path=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["web_path"])' "$bundle")
/usr/local/x-ui/x-ui setting -username "$username" -password "$password" -resetTwoFactor false >/dev/null 2>&1
/usr/local/x-ui/x-ui setting -webBasePath "$web_path" >/dev/null 2>&1
unset username password web_path
x-ui restart >/dev/null
for _ in $(seq 1 20); do
  if systemctl is-active --quiet x-ui && ss -lntH | grep -q ':8443 ' && ss -lntH | grep -q ':2096 '; then
    exit 0
  fi
  sleep 1
done
echo 'FAIL: x-ui listeners did not recover' >&2
exit 32
NLUPDATE

ssh "${SSH_OPTIONS[@]}" "$BOT" bash -s -- "$BOT_BUNDLE" "$SERVER_ID" <<'BOTUPDATE'
set -euo pipefail
bundle=$1
server_id=$2
cd /root/special-bot
docker run --rm --network vpn_bot_default --env-file .environment \
  -e DJANGO_SETTINGS_MODULE=bot.settings \
  -v "$bundle:/tmp/special-xui-rotation.json:ro" \
  vpnbot:latest python manage.py shell -c "
import json
from urllib.parse import urlsplit, urlunsplit
from apps.servers.models import Server
with open('/tmp/special-xui-rotation.json') as handle:
    data = json.load(handle)
server = Server.objects.get(id=$server_id)
parts = urlsplit(server.vpn_url)
server.vpn_username = data['username']
server.vpn_password = data['password']
server.vpn_url = urlunsplit((parts.scheme, parts.netloc, data['web_path'], '', ''))
server.save(update_fields=['vpn_username', 'vpn_password', 'vpn_url', 'updated_at'])
" >/dev/null
RUN_MIGRATIONS=false docker compose -f docker-compose.deploy.yml up -d --no-deps web celery celery_beat monitoring >/dev/null
if docker run --rm --network vpn_bot_default --env-file .environment \
  -e DJANGO_SETTINGS_MODULE=bot.settings vpnbot:latest python -c 'import django; django.setup(); from apps.telegram_bot.tasks import safe_broadcast_v1; assert safe_broadcast_v1.name == "apps.telegram_bot.tasks.safe_broadcast_v1"' >/dev/null; then
  docker compose -f docker-compose.deploy.yml up -d --no-deps broadcast >/dev/null
else
  docker compose -f docker-compose.deploy.yml stop broadcast >/dev/null 2>&1 || true
  docker compose -f docker-compose.deploy.yml rm -sf broadcast >/dev/null 2>&1 || true
  echo 'BROADCAST_QUARANTINED: image lacks safe_broadcast_v1; worker left stopped' >&2
fi
for _ in $(seq 1 30); do
  if docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/tmp/special-rotation-audit.out 2>&1; then
    break
  fi
  sleep 2
done
tail -1 /tmp/special-rotation-audit.out | grep -qx 'Legacy VPN audit passed.'
# The legacy audit proves a login plus inbound read using the new URL/path and
# credentials. Verify the exact configured server once more without printing it.
timeout 60 docker exec special-bot-web-1 python manage.py shell -c '
import asyncio
from apps.servers.models import Server
from utils.py3xui.async_api import AsyncApi
async def check():
    server = await Server.objects.aget(id=1)
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password, use_tls_verify=False)
    await api.login()
    inbound = await api.inbound.get_by_id(server.inbound_id)
    assert inbound.id == server.inbound_id
asyncio.run(check())
' >/dev/null
rm -f /tmp/special-rotation-audit.out
timeout 90 docker exec special-bot-web-1 python manage.py audit_xui_sub_id_coverage --server-id "$server_id" >/dev/null
timeout 30 docker exec special-bot-web-1 python manage.py shell -c '
from apps.monitoring.models import MonitorState
states = {state.layer: state for state in MonitorState.objects.all()}
assert set(states) >= {"l0", "l1", "l2"}
assert all(states[layer].last_ok and not states[layer].alert for layer in ("l0", "l1", "l2"))
print("monitoring=healthy")
' >/dev/null
BOTUPDATE

ssh "${SSH_OPTIONS[@]}" "$NL" "test \"\$(stat -c '%a' /etc/x-ui/x-ui.db)\" = 600" >/dev/null
trap - EXIT
ssh "${SSH_OPTIONS[@]}" "$NL" "rm -f -- '$NL_BUNDLE' '$NL_BACKUP'" >/dev/null 2>&1 || true
ssh "${SSH_OPTIONS[@]}" "$BOT" "rm -f -- '$BOT_BUNDLE' '$BOT_BACKUP'" >/dev/null 2>&1 || true
cleanup_local
echo 'rotation=passed'
