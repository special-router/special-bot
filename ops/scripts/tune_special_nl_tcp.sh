#!/usr/bin/env bash
set -euo pipefail

NL_HOST=${SPECIAL_NL_HOST:-195.66.213.74}
SSH_KEY=${SPECIAL_SSH_KEY:-$HOME/.ssh/id_ed25519}
MODE=${1:-verify}
SSH_OPTIONS=(
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10
  -o ConnectionAttempts=1
)

case "$MODE" in
  verify)
    ssh "${SSH_OPTIONS[@]}" "root@$NL_HOST" bash -s <<'REMOTE'
set -euo pipefail
[[ $(sysctl -n net.core.default_qdisc) == fq ]]
[[ $(sysctl -n net.ipv4.tcp_congestion_control) == bbr ]]
grep -q '^tcp_bbr ' /proc/modules
[[ $(stat -c '%a' /etc/sysctl.d/99-special-vless-tuning.conf) == 644 ]]
grep -Eq '^net\.core\.default_qdisc[[:space:]]*=[[:space:]]*fq$' /etc/sysctl.d/99-special-vless-tuning.conf
grep -Eq '^net\.ipv4\.tcp_congestion_control[[:space:]]*=[[:space:]]*bbr$' /etc/sysctl.d/99-special-vless-tuning.conf
systemctl is-active --quiet x-ui
[[ $(ss -lntpH 'sport = :8443' | wc -l) -eq 1 ]]
echo 'nl_tcp_tuning=verified qdisc=fq congestion_control=bbr listeners_8443=1'
REMOTE
    ;;
  apply)
    [[ ${SPECIAL_APPROVE_NL_TCP_TUNING:-} == YES ]] || {
      echo 'BLOCK: set SPECIAL_APPROVE_NL_TCP_TUNING=YES' >&2
      exit 2
    }
    ssh "${SSH_OPTIONS[@]}" "root@$NL_HOST" bash -s <<'REMOTE'
set -euo pipefail
path=/etc/sysctl.d/99-special-vless-tuning.conf
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$path.bak.$stamp"
old_qdisc=$(sysctl -n net.core.default_qdisc)
old_cc=$(sysctl -n net.ipv4.tcp_congestion_control)
rollback() {
  rc=$?
  trap - EXIT
  if [[ $rc -ne 0 ]]; then
    if [[ -f "$backup" ]]; then
      cp --preserve=mode "$backup" "$path"
    else
      rm -f "$path"
    fi
    sysctl -w "net.core.default_qdisc=$old_qdisc" >/dev/null 2>&1 || true
    sysctl -w "net.ipv4.tcp_congestion_control=$old_cc" >/dev/null 2>&1 || true
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  exit "$rc"
}
trap rollback EXIT
systemctl is-active --quiet x-ui
[[ $(ss -lntpH 'sport = :8443' | wc -l) -eq 1 ]]
modprobe -n tcp_bbr >/dev/null
modprobe tcp_bbr
if [[ -f "$path" ]]; then
  cp --preserve=mode "$path" "$backup"
fi
cat > "$path" <<'EOF'
# SPECIAL VLESS data-plane tuning. Validate with protected Direct/Relay probes.
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
EOF
chmod 644 "$path"
sysctl -p "$path" >/dev/null
[[ $(sysctl -n net.core.default_qdisc) == fq ]]
[[ $(sysctl -n net.ipv4.tcp_congestion_control) == bbr ]]
grep -q '^tcp_bbr ' /proc/modules
systemctl is-active --quiet x-ui
[[ $(ss -lntpH 'sport = :8443' | wc -l) -eq 1 ]]
trap - EXIT
rm -f "$backup"
echo 'nl_tcp_tuning=applied qdisc=fq congestion_control=bbr listeners_8443=1'
REMOTE
    ;;
  *)
    echo 'usage: tune_special_nl_tcp.sh [verify|apply]' >&2
    exit 2
    ;;
esac
