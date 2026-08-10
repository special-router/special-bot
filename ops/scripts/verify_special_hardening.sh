#!/usr/bin/env bash
set -euo pipefail

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
NL_HOST=${SPECIAL_NL_HOST:-195.66.213.74}
SSH_KEY=${SPECIAL_SSH_KEY:-$HOME/.ssh/id_ed25519}
SSH_OPTS=(
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=15
)
REMOTE_TIMEOUT=${SPECIAL_HARDENING_REMOTE_TIMEOUT:-120}
EXPECTED_COMMIT=${SPECIAL_HARDENING_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}

remote() {
  local host=$1
  shift
  timeout "$REMOTE_TIMEOUT" ssh "${SSH_OPTS[@]}" "root@$host" "$@"
}

commit=$(remote "$BOT_HOST" 'cd /root/special-bot && git rev-parse --short HEAD')
origin=$(remote "$BOT_HOST" 'cd /root/special-bot && git rev-parse --short origin/main')
[[ $commit == "$EXPECTED_COMMIT" && $origin == "$EXPECTED_COMMIT" ]]

port_binding=$(remote "$BOT_HOST" 'docker port special-bot-web-1 8001/tcp')
[[ $port_binding == "$BOT_HOST:8001" ]]

swap_bytes=$(remote "$BOT_HOST" 'swapon --show --noheadings --bytes | awk "{print \$3}"')
(( swap_bytes >= 1073737728 ))
remote "$BOT_HOST" 'grep -qx "/swapfile none swap sw 0 0" /etc/fstab'

[[ $(remote "$BOT_HOST" 'systemctl is-enabled docker-user-firewall.service') == enabled ]]
[[ $(remote "$BOT_HOST" 'systemctl is-active docker-user-firewall.service') == active ]]
[[ $(remote "$BOT_HOST" 'iptables -S DOCKER-USER | grep -c -- "--dport 8001"') == 3 ]]

if timeout 6 curl -sS --connect-timeout 3 --max-time 5 "http://$BOT_HOST:8001/" -o /dev/null; then
  echo 'ERROR: BOT :8001 is reachable outside NL' >&2
  exit 1
fi

nl_status=$(remote "$NL_HOST" "curl -s --connect-timeout 5 --max-time 15 -H 'Host: sub.special-wifi.ru' http://$BOT_HOST:8001/sub/does-not-exist -o /dev/null -w '%{http_code}'")
[[ $nl_status == 404 ]]

legacy=$(remote "$BOT_HOST" 'docker exec special-bot-web-1 python manage.py audit_legacy_vpn 2>&1')
grep -q 'entitled_missing=0' <<<"$legacy"
grep -q 'Legacy VPN audit passed.' <<<"$legacy"

monitoring=$(remote "$BOT_HOST" 'docker exec special-bot-web-1 python manage.py audit_special_monitoring 2>&1')
for layer in l0 l1 l2; do
  grep -q "layer=$layer ok=true alert=false" <<<"$monitoring"
done

remote "$BOT_HOST" 'cd /root/special-bot && docker compose -f docker-compose.deploy.yml config >/dev/null'

printf 'commit=%s port=%s swap_bytes=%s nl_status=%s\n' "$commit" "$port_binding" "$swap_bytes" "$nl_status"
printf '%s\n' "$legacy" | tail -2
printf '%s\n' "$monitoring" | tail -3
echo 'SPECIAL hardening verification passed.'
