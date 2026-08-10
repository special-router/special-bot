#!/usr/bin/env bash
set -euo pipefail

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
NL_HOST=${SPECIAL_XUI_HOST:-195.66.213.74}
EXPECTED_COMMIT=${SPECIAL_PRODUCTION_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
PRIMARY_INBOUND_ID=${SPECIAL_PRIMARY_INBOUND_ID:-5}
STATUS_INBOUND_ID=${SPECIAL_STATUS_INBOUND_ID:-1}
MIRROR_INBOUND_ID=${SPECIAL_MIRROR_INBOUND_ID:-14}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NL_BACKUP="/etc/x-ui/x-ui.db.listener-consolidation.$STAMP.bak"

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
BOT="root@$BOT_HOST"
NL="root@$NL_HOST"

[[ ${SPECIAL_APPROVE_LISTENER_CONSOLIDATION:-} == YES ]] || {
  echo 'BLOCK: set SPECIAL_APPROVE_LISTENER_CONSOLIDATION=YES for this production cutover' >&2
  exit 2
}

ssh "${SSH_OPTIONS[@]}" "$BOT" 'echo bot_ssh=ok' >/dev/null
ssh "${SSH_OPTIONS[@]}" "$NL" 'echo nl_ssh=ok' >/dev/null

rollback() {
  rc=$?
  trap - EXIT
  if [[ $rc -ne 0 ]]; then
    ssh "${SSH_OPTIONS[@]}" "$NL" bash -s -- "$NL_BACKUP" <<'NLROLLBACK' >/dev/null 2>&1 || true
set -u
backup=$1
if [[ -f "$backup" ]]; then
  cp --preserve=mode "$backup" /etc/x-ui/x-ui.db
  chmod 600 /etc/x-ui/x-ui.db
  x-ui restart >/dev/null 2>&1 || true
fi
NLROLLBACK
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  exit "$rc"
}
trap rollback EXIT

ssh "${SSH_OPTIONS[@]}" "$BOT" bash -s -- "$EXPECTED_COMMIT" <<'BOTCHECK'
set -euo pipefail
expected_commit=$1
cd /root/special-bot
[[ $(git rev-parse --short HEAD) == "$expected_commit" ]] || { echo 'BLOCK: unexpected BOT commit'; exit 20; }
unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9A-Za-zT_-]+$/ {print}')
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected BOT checkout paths'; exit 21; }
load1=$(awk '{print $1}' /proc/loadavg)
cpus=$(nproc)
mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
blocked=$(ps -eo stat= | awk '$1 ~ /^D/ {n++} END {print n+0}')
awk -v load_value="$load1" -v cpus="$cpus" 'BEGIN {exit !(load_value <= cpus * 4)}' || exit 22
(( mem_kb >= 131072 )) || exit 23
(( blocked == 0 )) || exit 24
timeout 15 docker info >/dev/null
timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
timeout 60 docker exec special-bot-web-1 python manage.py audit_special_monitoring >/tmp/special-monitoring-preflight.out
grep -q 'layer=l0 ok=true' /tmp/special-monitoring-preflight.out
grep -q 'layer=l1 ok=true' /tmp/special-monitoring-preflight.out
rm -f /tmp/special-monitoring-preflight.out
BOTCHECK

ssh "${SSH_OPTIONS[@]}" "$NL" bash -s -- \
  "$NL_BACKUP" "$PRIMARY_INBOUND_ID" "$STATUS_INBOUND_ID" "$MIRROR_INBOUND_ID" <<'NLPREP'
set -euo pipefail
backup=$1
primary_id=$2
status_id=$3
mirror_id=$4
[[ $(stat -c '%a' /etc/x-ui/x-ui.db) == 600 ]] || { echo 'BLOCK: x-ui database mode'; exit 30; }
systemctl is-active --quiet x-ui || { echo 'BLOCK: x-ui inactive'; exit 31; }
umask 077
sqlite3 /etc/x-ui/x-ui.db ".backup '$backup'"
chmod 600 "$backup"
python3 - "$primary_id" "$status_id" "$mirror_id" <<'PY'
import json
import sqlite3
import sys

primary_id, status_id, mirror_id = map(int, sys.argv[1:])
connection = sqlite3.connect('/etc/x-ui/x-ui.db')
rows = {
    int(row[0]): row
    for row in connection.execute(
        'select id, enable, port, protocol, settings, stream_settings from inbounds where id in (?, ?, ?)',
        (primary_id, status_id, mirror_id),
    )
}
if set(rows) != {primary_id, status_id, mirror_id}:
    raise SystemExit('BLOCK: expected inbounds missing')
primary = rows[primary_id]
if not bool(primary[1]) or int(primary[2]) != 8443 or primary[3] != 'vless':
    raise SystemExit('BLOCK: primary inbound shape')
primary_stream = json.loads(primary[5] or '{}')
if primary_stream.get('network') != 'tcp' or primary_stream.get('security') != 'reality':
    raise SystemExit('BLOCK: primary stream shape')
for duplicate_id in (status_id, mirror_id):
    candidate = rows[duplicate_id]
    candidate_stream = json.loads(candidate[5] or '{}')
    if not bool(candidate[1]) or int(candidate[2]) != int(primary[2]) or candidate[3] != primary[3]:
        raise SystemExit('BLOCK: duplicate listener shape drift')
    if candidate_stream.get('realitySettings') != primary_stream.get('realitySettings'):
        raise SystemExit('BLOCK: Reality settings drift')
PY
NLPREP

# The protected canary's stored direct key is legacy no-flow. Normalize only
# that explicitly configured internal client before removing duplicate listeners;
# the NL database backup above covers this API mutation as part of rollback.
ssh "${SSH_OPTIONS[@]}" "$BOT" bash -s -- "$PRIMARY_INBOUND_ID" <<'BOTCANARY'
set -euo pipefail
primary_id=$1
cd /root/special-bot
timeout 90 docker exec special-bot-web-1 python manage.py shell -c "
import asyncio
from django.conf import settings
from apps.servers.models import Server
from apps.vpn.models import UserVPN
from utils.py3xui.async_api import AsyncApi
async def normalize():
    user_vpn = await UserVPN.objects.select_related('server').aget(pk=settings.SPECIAL_MONITOR_CANARY_USER_VPN_ID)
    if user_vpn.server.inbound_id != int('$primary_id') or 'flow=' in (user_vpn.vpn_key or ''):
        raise RuntimeError('canary_contract')
    server = await Server.objects.aget(id=user_vpn.server_id)
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password, use_tls_verify=False)
    await api.login()
    inbound = await api.inbound.get_by_id(server.inbound_id)
    matches = [client for client in inbound.settings.clients if str(client.id) == str(user_vpn.vpn_uuid)]
    if len(matches) != 1 or not matches[0].enable:
        raise RuntimeError('canary_client')
    client = matches[0]
    client.flow = ''
    client.inbound_id = server.inbound_id
    await api.client.update(str(user_vpn.vpn_uuid), client)
    refreshed = await api.inbound.get_by_id(server.inbound_id)
    current = [item for item in refreshed.settings.clients if str(item.id) == str(user_vpn.vpn_uuid)]
    if len(current) != 1 or current[0].flow:
        raise RuntimeError('canary_flow')
asyncio.run(normalize())
" >/dev/null
BOTCANARY

ssh "${SSH_OPTIONS[@]}" "$NL" bash -s -- \
  "$PRIMARY_INBOUND_ID" "$STATUS_INBOUND_ID" "$MIRROR_INBOUND_ID" <<'NLCUTOVER'
set -euo pipefail
primary_id=$1
status_id=$2
mirror_id=$3
python3 - "$primary_id" "$status_id" "$mirror_id" <<'PY'
import sqlite3
import sys

primary_id, status_id, mirror_id = map(int, sys.argv[1:])
connection = sqlite3.connect('/etc/x-ui/x-ui.db')
connection.execute('update inbounds set enable = 0 where id in (?, ?)', (status_id, mirror_id))
connection.commit()
states = dict(connection.execute('select id, enable from inbounds where id in (?, ?, ?)', (primary_id, status_id, mirror_id)))
if states != {status_id: 0, primary_id: 1, mirror_id: 0}:
    raise SystemExit('FAIL: inbound enable state')
PY
x-ui restart >/dev/null
for _ in $(seq 1 25); do
  if systemctl is-active --quiet x-ui && [[ $(ss -lntpH 'sport = :8443' | wc -l) -eq 1 ]]; then
    break
  fi
  sleep 1
done
systemctl is-active --quiet x-ui
[[ $(ss -lntpH 'sport = :8443' | wc -l) -eq 1 ]] || { echo 'FAIL: expected one :8443 listener'; exit 32; }
python3 - "$primary_id" "$status_id" "$mirror_id" <<'PY'
import sqlite3
import sys
primary_id, status_id, mirror_id = map(int, sys.argv[1:])
connection = sqlite3.connect('/etc/x-ui/x-ui.db')
states = dict(connection.execute('select id, enable from inbounds where id in (?, ?, ?)', (primary_id, status_id, mirror_id)))
if states != {status_id: 0, primary_id: 1, mirror_id: 0}:
    raise SystemExit('FAIL: persisted inbound enable state')
for inbound_id in (status_id, primary_id, mirror_id):
    settings = connection.execute('select settings from inbounds where id=?', (inbound_id,)).fetchone()[0]
    if not settings:
        raise SystemExit('FAIL: client metadata missing')
print('listener_state=consolidated records_preserved=true')
PY
NLCUTOVER

ssh "${SSH_OPTIONS[@]}" "$BOT" bash -s <<'BOTVERIFY'
set -euo pipefail
cd /root/special-bot
timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
timeout 90 docker exec special-bot-web-1 python manage.py audit_xui_sub_id_coverage --server-id 1 >/dev/null
for attempt in 1 2 3; do
  timeout 90 docker exec special-bot-web-1 python -c '
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bot.settings")
django.setup()
from apps.monitoring.models import MonitorState
from apps.monitoring.tasks import run_protocol_monitor
MonitorState.objects.filter(layer="l2").delete()
run_protocol_monitor.delay()
'
  sleep 40
  timeout 60 docker exec special-bot-web-1 python manage.py audit_special_monitoring >/tmp/special-monitoring-post.out
  grep -q 'layer=l0 ok=true' /tmp/special-monitoring-post.out
  grep -q 'layer=l1 ok=true' /tmp/special-monitoring-post.out
  if grep -q 'layer=l2 ok=true' /tmp/special-monitoring-post.out; then
    rm -f /tmp/special-monitoring-post.out
    exit 0
  fi
done
cat /tmp/special-monitoring-post.out >&2
rm -f /tmp/special-monitoring-post.out
exit 40
BOTVERIFY

trap - EXIT
ssh "${SSH_OPTIONS[@]}" "$NL" "rm -f -- '$NL_BACKUP'" >/dev/null
echo 'listener_consolidation=passed'
