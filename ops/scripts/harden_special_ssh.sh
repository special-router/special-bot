#!/usr/bin/env bash
set -euo pipefail

HOST=${1:-}
[[ -n "$HOST" ]] || { echo "Usage: $0 <approved-host>" >&2; exit 2; }
[[ ${SPECIAL_SSH_HARDEN_APPROVED:-false} == true ]] || {
  echo 'BLOCK: set SPECIAL_SSH_HARDEN_APPROVED=true only with a retained rollback session' >&2
  exit 10
}
SSH_KEY=${SPECIAL_SSH_KEY:-$HOME/.ssh/id_ed25519}
SSH=(
  ssh -i "$SSH_KEY"
  -o BatchMode=yes
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10
  "root@$HOST"
)

# Independent preflight connection. Keep a separate provider-console/SSH rollback
# session open; this script cannot create or verify that human-controlled session.
"${SSH[@]}" 'sshd -t; test -s /root/.ssh/authorized_keys; test "$(stat -c %a /root/.ssh/authorized_keys)" = 600'

"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
stamp=$(date -u +%Y%m%dT%H%M%SZ)
unit=ssh
systemctl list-unit-files ssh.service >/dev/null 2>&1 || unit=sshd
backup="/etc/ssh/sshd_config.d/99-special-hardening.conf.bak.$stamp"
config=/etc/ssh/sshd_config.d/99-special-hardening.conf
[[ ! -f "$config" ]] || cp --preserve=mode "$config" "$backup"
cat > "$config" <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
EOF
chmod 600 "$config"
rollback() {
  rc=$?
  if [[ $rc -ne 0 ]]; then
    if [[ -f "$backup" ]]; then cp --preserve=mode "$backup" "$config"; else rm -f "$config"; fi
    sshd -t && systemctl reload "$unit" || true
    echo "ROLLBACK_ATTEMPTED rc=$rc" >&2
  fi
  exit "$rc"
}
trap rollback EXIT
sshd -t
systemctl reload "$unit"
sshd -T | grep -Eq '^passwordauthentication no$'
sshd -T | grep -Eq '^kbdinteractiveauthentication no$'
sshd -T | grep -Eq '^pubkeyauthentication yes$'
sshd -T | grep -Eq '^permitrootlogin (prohibit-password|without-password)$'
trap - EXIT
echo ssh_hardening=applied
REMOTE

# Prove a new key-only connection works before the operator closes rollback.
"${SSH[@]}" 'echo key_only_postcheck=passed; sshd -t'
