#!/usr/bin/env bash
# Bootstrap exception: root SSH is used only before PermitRootLogin no is applied.
# No production mutation occurs unless SPECIAL_SSH_HARDEN_APPROVED=true is explicit.
set -euo pipefail

HOST=${1:-}
[[ -n $HOST ]] || { echo "Usage: $0 <approved-host>" >&2; exit 2; }
[[ ${SPECIAL_SSH_HARDEN_APPROVED:-false} == true ]] || {
  echo 'BLOCK: set SPECIAL_SSH_HARDEN_APPROVED=true for one approved host window' >&2
  exit 10
}
SSH_KEY=${SPECIAL_SSH_KEY:-$HOME/.ssh/id_ed25519}
OPERATOR_KEY=${SPECIAL_SSH_OPERATOR_KEY:-$SSH_KEY}
OPERATOR_PUBLIC_KEY_FILE=${SPECIAL_SSH_OPERATOR_PUBLIC_KEY_FILE:-$OPERATOR_KEY.pub}
OPERATOR_USER=${SPECIAL_SSH_USER:-specialops}
[[ $OPERATOR_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'BLOCK: invalid SPECIAL_SSH_USER' >&2; exit 2; }
[[ -r $SSH_KEY && -r $OPERATOR_KEY && -r $OPERATOR_PUBLIC_KEY_FILE ]] || {
  echo 'BLOCK: approved SSH private/public key is unreadable' >&2
  exit 11
}
[[ $(stat -c %a "$SSH_KEY") =~ ^(400|600)$ && $(stat -c %a "$OPERATOR_KEY") =~ ^(400|600)$ ]] || {
  echo 'BLOCK: approved SSH private key mode must be 0400 or 0600' >&2
  exit 12
}
read -r OPERATOR_PUBLIC_KEY_TYPE OPERATOR_PUBLIC_KEY_BODY _ < "$OPERATOR_PUBLIC_KEY_FILE"
[[ $OPERATOR_PUBLIC_KEY_TYPE == ssh-ed25519 && $OPERATOR_PUBLIC_KEY_BODY =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || {
  echo 'BLOCK: SPECIAL_SSH_OPERATOR_PUBLIC_KEY_FILE must contain an ED25519 public key' >&2
  exit 13
}
OPERATOR_PUBLIC_KEY="$OPERATOR_PUBLIC_KEY_TYPE $OPERATOR_PUBLIC_KEY_BODY"
DERIVED_PUBLIC_KEY=$(SSH_ASKPASS_REQUIRE=never ssh-keygen -y -f "$OPERATOR_KEY" </dev/null) || {
  echo 'BLOCK: operator private key must be non-interactively readable' >&2
  exit 15
}
read -r DERIVED_PUBLIC_KEY_TYPE DERIVED_PUBLIC_KEY_BODY _ <<<"$DERIVED_PUBLIC_KEY"
[[ "$DERIVED_PUBLIC_KEY_TYPE $DERIVED_PUBLIC_KEY_BODY" == "$OPERATOR_PUBLIC_KEY" ]] || {
  echo 'BLOCK: operator public key does not match the private key' >&2
  exit 16
}
unset DERIVED_PUBLIC_KEY

FRESH_OPTS=(
  -o IdentitiesOnly=yes -o BatchMode=yes
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
  -o PreferredAuthentications=publickey -o ControlMaster=no -o ControlPath=none
  -o StrictHostKeyChecking=yes -o ConnectTimeout=10
)
ROOT_TARGET="root@$HOST"
quote_arg() { printf '%q' "$1"; }
if [[ $HOST == "${SPECIAL_BOT_HOST:-72.56.23.226}" ]]; then
  SERVICE_ROLE=bot
elif [[ $HOST == "${SPECIAL_NL_HOST:-${SPECIAL_XUI_HOST:-195.66.213.74}}" ]]; then
  SERVICE_ROLE=nl
else
  echo 'BLOCK: host is not the approved BOT or NL target' >&2
  exit 14
fi
# A retained authenticated root channel supports immediate rollback. The on-host
# watchdog below independently restores the old config if that TCP channel dies.
CTL_DIR=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/special-ssh.XXXXXX")
chmod 700 "$CTL_DIR"
CTL="$CTL_DIR/cm"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ROLLBACK_UNIT="special-ssh-rollback-$STAMP"
WATCHDOG_ARMED=false

rollback_now() {
  local rc=$?
  trap - EXIT
  if [[ $WATCHDOG_ARMED == true ]]; then
    ssh -S "$CTL" "$ROOT_TARGET" \
      "systemctl stop '$ROLLBACK_UNIT.timer' >/dev/null 2>&1 || true; systemctl start '$ROLLBACK_UNIT.service'" \
      >/dev/null 2>&1 || true
  fi
  ssh -S "$CTL" -O exit "$ROOT_TARGET" >/dev/null 2>&1 || true
  rm -rf "$CTL_DIR"
  exit "$rc"
}
trap rollback_now EXIT

# Fresh root access is baseline proof only; it is intentionally rejected later.
ssh -i "$SSH_KEY" "${FRESH_OPTS[@]}" "$ROOT_TARGET" 'test "$(id -u)" = 0; /usr/sbin/sshd -t'
ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -o StrictHostKeyChecking=yes -o ControlMaster=yes -o ControlPath="$CTL" \
  -o ControlPersist=no -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -fN "$ROOT_TARGET"
ssh -S "$CTL" -O check "$ROOT_TARGET"

# Preflight actual daemon/inclusion semantics, provision the named account and
# arm a rollback timer before any sshd policy file is changed.
preflight_command="sudo -n bash -s -- $(quote_arg "$OPERATOR_USER") $(quote_arg "$OPERATOR_PUBLIC_KEY_TYPE") $(quote_arg "$OPERATOR_PUBLIC_KEY_BODY") $(quote_arg "$STAMP") $(quote_arg "$ROLLBACK_UNIT")"
ssh -S "$CTL" "$ROOT_TARGET" "$preflight_command" <<'REMOTE'
set -euo pipefail
operator_user=$1
operator_key="$2 $3"
stamp=$4
rollback_unit=$5
unit=ssh
systemctl list-unit-files ssh.service >/dev/null 2>&1 || unit=sshd
command -v systemd-run >/dev/null || { echo 'BLOCK: systemd-run unavailable'; exit 30; }
command -v visudo >/dev/null || { echo 'BLOCK: visudo unavailable'; exit 31; }
/usr/sbin/sshd -t
systemctl is-active --quiet "$unit"
# Do not guess precedence: reject custom daemon auth overrides, Match before the
# global Include, or any relevant value preceding the included drop-ins.
exec_start=$(systemctl show "$unit" -p ExecStart --value)
[[ $exec_start =~ ^\{[[:space:]]*path=/usr/sbin/sshd[[:space:]]*\;[[:space:]]*argv\[\]=/usr/sbin/sshd[[:space:]]+-D[[:space:]]+\$SSHD_OPTS[[:space:]]*\; ]] || {
  echo 'BLOCK: sshd ExecStart is not the audited distro command'; exit 32;
}
# The audited Debian/Ubuntu unit expands SSHD_OPTS from /etc/default/ssh.
# Reject any actual daemon argument until the verifier can pass it to sshd -T.
if [[ -r /etc/default/ssh ]]; then
  sshd_opts=$(bash -c 'set -a; . /etc/default/ssh; printf "%s" "${SSHD_OPTS:-}"')
  [[ -z $sshd_opts ]] || { echo 'BLOCK: nonempty service SSHD_OPTS is unsupported'; exit 32; }
fi
[[ -z $(systemctl show "$unit" -p Environment --value) ]] || {
  echo 'BLOCK: ssh service environment overrides are unsupported'; exit 32;
}
grep -Eq '^[[:space:]]*Include[[:space:]].*sshd_config\.d' /etc/ssh/sshd_config || {
  echo 'BLOCK: no global sshd_config.d Include'; exit 33;
}
awk '
  /^[[:space:]]*Match([[:space:]]|$)/ { exit 34 }
  /^[[:space:]]*Include[[:space:]].*sshd_config\.d/ { seen=1; next }
  /^[[:space:]]*(PasswordAuthentication|KbdInteractiveAuthentication|ChallengeResponseAuthentication|PubkeyAuthentication|PermitRootLogin)[[:space:]]/ && !seen { exit 35 }
  END { if (!seen) exit 33 }
' /etc/ssh/sshd_config || { echo 'BLOCK: sshd first-value precedence not provably global'; exit 34; }
config=/etc/ssh/sshd_config.d/00-special-hardening.conf
[[ ! -e $config || -f $config && ! -L $config && $(stat -c '%U:%G' "$config") == root:root ]] || {
  echo 'BLOCK: unexpected hardening drop-in type/owner'; exit 35;
}
created=false
if ! id "$operator_user" >/dev/null 2>&1; then
  useradd --create-home --user-group --shell /bin/bash "$operator_user"
  created=true
fi
# Never silently adopt an existing account: its exact identity, one key, locked
# password and isolated sudoers policy must already match this approved key.
test "$(id -u "$operator_user")" -ne 0
test "$(getent passwd "$operator_user" | cut -d: -f6)" = "/home/$operator_user"
test "$(getent passwd "$operator_user" | cut -d: -f7)" = /bin/bash
if [[ $created == true ]]; then
  usermod --lock "$operator_user"
  install -d -o "$operator_user" -g "$operator_user" -m 0700 "/home/$operator_user/.ssh"
  key_tmp=$(mktemp "/home/$operator_user/.ssh/.authorized_keys.XXXXXX")
  printf '%s\n' "$operator_key" > "$key_tmp"
  chown "$operator_user:$operator_user" "$key_tmp"
  chmod 0600 "$key_tmp"
  mv -fT "$key_tmp" "/home/$operator_user/.ssh/authorized_keys"
else
  passwd -S "$operator_user" | awk '$2 == "L" || $2 == "LK" {ok=1} END {exit !ok}' || {
    echo 'BLOCK: existing operator password is not locked'; exit 36;
  }
fi
test ! -L "/home/$operator_user/.ssh/authorized_keys"
test "$(stat -c '%U:%G:%a' "/home/$operator_user/.ssh")" = "$operator_user:$operator_user:700"
test "$(stat -c '%U:%G:%a' "/home/$operator_user/.ssh/authorized_keys")" = "$operator_user:$operator_user:600"
test "$(wc -l < "/home/$operator_user/.ssh/authorized_keys")" -eq 1
grep -Fxq "$operator_key" "/home/$operator_user/.ssh/authorized_keys" || {
  echo 'BLOCK: existing operator key does not exactly match approved key'; exit 37;
}
sudo_path="/etc/sudoers.d/90-$operator_user"
if [[ -e $sudo_path ]]; then
  [[ -f $sudo_path && ! -L $sudo_path && $(stat -c '%U:%G:%a' "$sudo_path") == root:root:440 ]] || {
    echo 'BLOCK: unexpected operator sudoers file'; exit 38;
  }
  grep -Fxq "$operator_user ALL=(ALL:ALL) NOPASSWD: ALL" "$sudo_path" || {
    echo 'BLOCK: existing operator sudoers policy differs'; exit 39;
  }
else
  sudo_tmp=$(mktemp /etc/sudoers.d/.specialops.XXXXXX)
  printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$operator_user" > "$sudo_tmp"
  chmod 0440 "$sudo_tmp"; chown root:root "$sudo_tmp"
  visudo -cf "$sudo_tmp"
  mv -fT "$sudo_tmp" "$sudo_path"
fi
visudo -c
backup_dir=/root/special-ssh-hardening-backups
install -d -o root -g root -m 0700 "$backup_dir"
backup="$backup_dir/$stamp.conf"
had_config=false
if [[ -f $config ]]; then cp --preserve=mode "$config" "$backup"; had_config=true; fi
rollback_script="$backup_dir/$stamp.rollback.sh"
cat > "$rollback_script" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ $had_config == true ]]; then
  cp --preserve=mode '$backup' '$config'
else
  rm -f -- '$config'
fi
/usr/sbin/sshd -t
systemctl reload '$unit'
EOF
chmod 0700 "$rollback_script"
bash -n "$rollback_script"
systemd-run --unit="$rollback_unit" --on-active=15m --property=Type=oneshot /bin/bash "$rollback_script"
systemctl is-active --quiet "$rollback_unit.timer"
echo "hardening_unit=$unit watchdog=$rollback_unit"
REMOTE
WATCHDOG_ARMED=true

# A new TCP connection must work before changing sshd, not merely the master.
ssh -i "$OPERATOR_KEY" "${FRESH_OPTS[@]}" "${OPERATOR_USER}@$HOST" \
  'test "$(id -u)" -ne 0; test "$(sudo -n id -u)" = 0; sudo -n true'
ssh -S "$CTL" -O check "$ROOT_TARGET"

apply_command="sudo -n bash -s -- $(quote_arg "$OPERATOR_USER") $(quote_arg "$ROLLBACK_UNIT")"
ssh -S "$CTL" "$ROOT_TARGET" "$apply_command" <<'REMOTE'
set -euo pipefail
operator_user=$1
rollback_unit=$2
unit=ssh
systemctl list-unit-files ssh.service >/dev/null 2>&1 || unit=sshd
config=/etc/ssh/sshd_config.d/00-special-hardening.conf
tmp=$(mktemp /etc/ssh/sshd_config.d/.00-special-hardening.XXXXXX)
cat > "$tmp" <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
EOF
chown root:root "$tmp"; chmod 0600 "$tmp"
mv -fT "$tmp" "$config"
/usr/sbin/sshd -t
# Evaluate actual Match behavior for this client/server tuple before reload.
read -r client_ip client_port server_ip server_port <<<"${SSH_CONNECTION:?missing SSH_CONNECTION}"
for user in "$operator_user" root; do
  effective=$(/usr/sbin/sshd -T -C "user=$user,addr=$client_ip,host=$client_ip,laddr=$server_ip,lport=$server_port")
  grep -qx 'passwordauthentication no' <<<"$effective"
  grep -qx 'kbdinteractiveauthentication no' <<<"$effective"
  grep -qx 'pubkeyauthentication yes' <<<"$effective"
  grep -qx 'permitrootlogin no' <<<"$effective"
  grep -qx 'authenticationmethods any' <<<"$effective"
  grep -qx 'gssapiauthentication no' <<<"$effective"
  grep -qx 'hostbasedauthentication no' <<<"$effective"
  grep -qx 'authorizedkeyscommand none' <<<"$effective"
  grep -qx 'trustedusercakeys none' <<<"$effective"
done
systemctl reload "$unit"
systemctl is-active --quiet "$unit"
ss -lnt '( sport = :22 )' | grep -q ':22'
echo "reload_stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REMOTE

# Fresh, non-multiplexed positive and negative authentication proofs.
ssh -i "$OPERATOR_KEY" "${FRESH_OPTS[@]}" "${OPERATOR_USER}@$HOST" \
  'test "$(id -u)" -ne 0; test "$(sudo -n id -u)" = 0; sudo -n -l >/dev/null'
if ssh -i "$SSH_KEY" "${FRESH_OPTS[@]}" "$ROOT_TARGET" true; then
  echo 'FAIL: fresh root SSH remained accepted; rolling back' >&2
  exit 40
fi
ssh -S "$CTL" -O check "$ROOT_TARGET"

# Read-only service gates run before rollback can be disarmed.
service_command="sudo -n bash -s -- $(quote_arg "$SERVICE_ROLE")"
ssh -i "$OPERATOR_KEY" "${FRESH_OPTS[@]}" "${OPERATOR_USER}@$HOST" \
  "$service_command" <<'REMOTE'
set -euo pipefail
role=$1
unit=ssh; systemctl list-unit-files ssh.service >/dev/null 2>&1 || unit=sshd
systemctl is-active --quiet "$unit"
ss -lnt '( sport = :22 )' | grep -q ':22'
case "$role" in
  bot)
    systemctl is-active --quiet docker
    timeout 15 docker info --format '{{.ServerVersion}}' >/dev/null
    docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1 | grep -qx true
    docker inspect -f '{{.State.Running}}' vpn_bot-redis-1 | grep -qx true
    timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
    timeout 90 docker exec special-bot-web-1 python manage.py audit_special_monitoring >/dev/null
    ;;
  nl)
    systemctl is-active --quiet nginx
    systemctl is-active --quiet x-ui
    nginx -t >/dev/null
    [[ $(ss -lntpH 'sport = :8443' | wc -l) -eq 1 ]]
    ;;
esac
if journalctl -u "$unit" --since '10 minutes ago' --no-pager | grep -Eqi '(fatal|configuration error)'; then
  echo 'BLOCK: ssh service journal error' >&2
  exit 42
fi
REMOTE

# Disarm only after all access/rejection/service gates succeeded and prove the
# rollback service never fired. Then repeat effective policy and fresh auth gates.
disarm_command="bash -s -- $(quote_arg "$ROLLBACK_UNIT") $(quote_arg "$OPERATOR_USER")"
ssh -S "$CTL" "$ROOT_TARGET" "$disarm_command" <<'REMOTE'
set -euo pipefail
rollback_unit=$1
operator_user=$2
systemctl stop "$rollback_unit.timer"
test "$(systemctl is-active "$rollback_unit.timer" || true)" != active
if systemctl list-jobs --no-legend "$rollback_unit.service" 2>/dev/null | grep -q . || \
   [[ $(systemctl is-active "$rollback_unit.service" || true) == active ]] || \
   [[ -n $(systemctl show "$rollback_unit.service" -p ExecMainStartTimestamp --value 2>/dev/null || true) ]]; then
  echo 'BLOCK: rollback watchdog already fired or is queued' >&2
  exit 43
fi
systemctl reset-failed "$rollback_unit.timer" "$rollback_unit.service" >/dev/null 2>&1 || true
read -r client_ip client_port server_ip server_port <<<"${SSH_CONNECTION:?missing SSH_CONNECTION}"
for user in "$operator_user" root; do
  effective=$(/usr/sbin/sshd -T -C "user=$user,addr=$client_ip,host=$client_ip,laddr=$server_ip,lport=$server_port")
  grep -qx 'passwordauthentication no' <<<"$effective"
  grep -qx 'kbdinteractiveauthentication no' <<<"$effective"
  grep -qx 'pubkeyauthentication yes' <<<"$effective"
  grep -qx 'permitrootlogin no' <<<"$effective"
done
REMOTE
ssh -i "$OPERATOR_KEY" "${FRESH_OPTS[@]}" "${OPERATOR_USER}@$HOST" \
  'test "$(id -u)" -ne 0; test "$(sudo -n id -u)" = 0'
if ssh -i "$SSH_KEY" "${FRESH_OPTS[@]}" "$ROOT_TARGET" true; then
  echo 'FAIL: fresh root SSH was accepted after watchdog disarm' >&2
  exit 44
fi
WATCHDOG_ARMED=false
ssh -S "$CTL" -O exit "$ROOT_TARGET"
rm -rf "$CTL_DIR"
trap - EXIT
echo "ssh_hardening=applied operator=$OPERATOR_USER root_fresh=rejected"
