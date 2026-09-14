#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HOST=${SPECIAL_CONFIG_EDGE_HOST:-158.160.187.90}
USER=${SPECIAL_CONFIG_EDGE_SSH_USER:-admin-user}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10 -o ConnectionAttempts=1 "$USER@$HOST")

upload="/tmp/special-config-edge-upload-$$"
cleanup() {
  "${SSH[@]}" sudo -n rm -rf -- "$upload" >/dev/null 2>&1 || true
}
trap cleanup EXIT
tar -C "$ROOT" -cf - \
  ops/config_edge_cache.py \
  ops/systemd/special-config-edge.service \
  ops/nginx/special-wifi.link.conf |
  "${SSH[@]}" "sudo -n bash -c '
    set -euo pipefail
    upload=\$1
    [[ \$upload =~ ^/tmp/special-config-edge-upload-[0-9]+\$ ]]
    mkdir -m 0700 \"\$upload\"
    tar -xf - -C \"\$upload\"
  ' -- $upload"

"${SSH[@]}" sudo -n bash -s -- "$upload" <<'REMOTE'
set -euo pipefail
work=$1
[[ $work =~ ^/tmp/special-config-edge-upload-[0-9]+$ ]] || exit 20

script=/opt/special/config_edge_cache.py
unit=/etc/systemd/system/special-config-edge.service
nginx=/etc/nginx/sites-available/special-wifi.link
mkdir -p /opt/special
for path in "$script" "$unit" "$nginx"; do
  if [[ -e "$path" ]]; then
    printf '%s' present >"$work/$(basename "$path").state"
    cp -a "$path" "$work/$(basename "$path").backup"
  else
    printf '%s' absent >"$work/$(basename "$path").state"
  fi
done

rollback() {
  rc=$?
  if [[ $rc -ne 0 ]]; then
    for path in "$script" "$unit" "$nginx"; do
      name=$(basename "$path")
      if [[ $(cat "$work/$name.state") == present ]]; then
        cp -a "$work/$name.backup" "$path"
      else
        rm -f "$path"
      fi
    done
    systemctl daemon-reload || true
    if [[ $(cat "$work/$(basename "$unit").state") == present ]]; then
      systemctl restart special-config-edge.service || true
    else
      systemctl disable --now special-config-edge.service >/dev/null 2>&1 || true
    fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  exit "$rc"
}
trap rollback EXIT

install -o root -g root -m 0755 "$work/ops/config_edge_cache.py" "$script"
install -o root -g root -m 0644 "$work/ops/systemd/special-config-edge.service" "$unit"
install -o root -g root -m 0644 "$work/ops/nginx/special-wifi.link.conf" "$nginx"
systemctl daemon-reload
systemctl enable --now special-config-edge.service >/dev/null
for _ in $(seq 1 20); do
  curl -fsS --max-time 2 http://127.0.0.1:18081/_health >/dev/null && break
  sleep 1
done
curl -fsS --max-time 2 http://127.0.0.1:18081/_health >/dev/null
nginx -t
systemctl reload nginx
public_origin=https://special-wifi.link
code=$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' \
  "$public_origin/sub/does-not-exist")
[[ $code == 404 ]] || { echo "FAIL: public edge expected 404 got $code"; exit 30; }
systemctl is-active --quiet special-config-edge.service
systemctl is-active --quiet nginx
echo 'config_edge_deployed service=active nginx=active unknown=404'
trap - EXIT
rm -rf "$work"
REMOTE
trap - EXIT
