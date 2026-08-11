#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
NL_HOST=${SPECIAL_NL_HOST:-195.66.213.74}
SSH_KEY=${SPECIAL_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
special_ssh_require_abs_path "$REMOTE_PATH" SPECIAL_BOT_REMOTE_PATH
REMOTE_PATH_Q=$(special_shell_quote "$REMOTE_PATH")
SSH_OPTS=(
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=15
)
REMOTE_TIMEOUT=${SPECIAL_HARDENING_REMOTE_TIMEOUT:-120}
EXPECTED_COMMIT=${SPECIAL_HARDENING_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
special_require_commit "$EXPECTED_COMMIT" SPECIAL_EXPECTED_COMMIT

remote() {
  local host=$1 command=$2
  local user=$SPECIAL_NL_SSH_USER
  [[ $host == "$BOT_HOST" ]] && user=$SPECIAL_BOT_SSH_USER
  printf '%s\n' "$command" | timeout "$REMOTE_TIMEOUT" ssh "${SSH_OPTS[@]}" \
    "$(special_ssh_target "$user" "$host")" \
    "sudo -n env SPECIAL_BOT_REMOTE_PATH=$REMOTE_PATH_Q bash -s"
}

commit=$(remote "$BOT_HOST" 'cd "$SPECIAL_BOT_REMOTE_PATH" && git rev-parse --short HEAD')
origin=$(remote "$BOT_HOST" 'cd "$SPECIAL_BOT_REMOTE_PATH" && git rev-parse --short origin/main')
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
for layer in host l0 l1 l2; do
  grep -q "layer=$layer ok=true alert=false" <<<"$monitoring"
done

remote "$BOT_HOST" 'cd "$SPECIAL_BOT_REMOTE_PATH" && docker compose -f docker-compose.deploy.yml config >/dev/null'
scale_readiness=$(remote "$BOT_HOST" 'cd "$SPECIAL_BOT_REMOTE_PATH" && docker exec special-bot-web-1 python manage.py validate_scale_readiness --json --origins-file ops/origins.example.json')
python3 - "$scale_readiness" <<'PY'
import json, sys
report = json.loads(sys.argv[1])
assert report['subscription_coverage_complete'] is True
assert report['monitoring_complete'] is True
assert report['duplicate_sub_ids'] == 0
assert report['redundancy_ready'] is False
assert report['legacy_retirement_ready'] is False
print('scale_readiness=bounded_pass')
PY

printf 'commit=%s port=%s swap_bytes=%s nl_status=%s\n' "$commit" "$port_binding" "$swap_bytes" "$nl_status"
printf '%s\n' "$legacy" | tail -2
printf '%s\n' "$monitoring" | tail -4
echo 'SPECIAL hardening verification passed.'
