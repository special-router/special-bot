#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

usage() {
  echo "Usage: $0 [--apply] <mode-0600-user-vpn-id-file>" >&2
  exit 2
}

APPLY=false
if [[ ${1:-} == --apply ]]; then
  APPLY=true
  shift
fi
[[ $# -eq 1 ]] || usage
IDS_FILE=$1
[[ -f "$IDS_FILE" ]] || { echo 'BLOCK: ID file not found' >&2; exit 10; }
[[ $(stat -c '%a' "$IDS_FILE") == 600 ]] || { echo 'BLOCK: ID file must be mode 0600' >&2; exit 11; }
awk 'NF != 1 || $1 !~ /^[0-9]+$/ {exit 1}' "$IDS_FILE" || {
  echo 'BLOCK: ID file must contain one numeric UserVPN ID per line' >&2
  exit 12
}
[[ -s "$IDS_FILE" ]] || { echo 'BLOCK: ID file is empty' >&2; exit 13; }
[[ $(sort -n "$IDS_FILE" | uniq -d | wc -l) -eq 0 ]] || {
  echo 'BLOCK: ID file contains duplicate records' >&2
  exit 14
}

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SERVER_ID=${SPECIAL_SERVER_ID:-1}
EXPECTED_COMMIT=${SPECIAL_SUBSCRIPTION_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
special_require_commit "$EXPECTED_COMMIT" SPECIAL_EXPECTED_COMMIT
BATCH_SIZE=${SPECIAL_SUBID_BATCH_SIZE:-5}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
special_ssh_require_tmp_dir
REMOTE_STAGE_DIR=
REMOTE_IDS=

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
if [[ -n "${SPECIAL_BOT_SSH_JUMP:-}" ]]; then
  SSH_OPTIONS+=(-J "$SPECIAL_BOT_SSH_JUMP")
fi
TARGET="$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "$BOT_HOST")"

cleanup_remote() {
  ssh "${SSH_OPTIONS[@]}" "$TARGET" "sudo -n rm -rf -- '$REMOTE_STAGE_DIR'" >/dev/null 2>&1 || true
}
trap cleanup_remote EXIT
REMOTE_STAGE_DIR=$(ssh "${SSH_OPTIONS[@]}" "$TARGET" "sudo -n mktemp -d -p '$SPECIAL_SSH_TMP_DIR' .special-subid-batch.XXXXXXXX.d")
[[ $REMOTE_STAGE_DIR == "$SPECIAL_SSH_TMP_DIR"/.special-subid-batch.*.d ]]
ssh "${SSH_OPTIONS[@]}" "$TARGET" sudo -n chown "$SPECIAL_BOT_SSH_USER:$SPECIAL_BOT_SSH_USER" "$REMOTE_STAGE_DIR"
REMOTE_IDS="$REMOTE_STAGE_DIR/ids"
scp "${SSH_OPTIONS[@]}" "$IDS_FILE" "$TARGET:$REMOTE_IDS" >/dev/null
ssh "${SSH_OPTIONS[@]}" "$TARGET" sudo -n bash -s -- \
  "$REMOTE_IDS" "$SERVER_ID" "$EXPECTED_COMMIT" "$BATCH_SIZE" "$APPLY" <<'REMOTE'
set -euo pipefail
ids_file=$1
server_id=$2
expected_commit=$3
batch_size=$4
apply=$5
trap 'rm -f "$ids_file"' EXIT
chmod 600 "$ids_file"
cd /root/special-bot
unexpected=$(git status --porcelain | awk '$2 != ".environment" && $2 !~ /^\.environment\.bak\.[0-9A-Za-zT_-]+$/ {print}')
[[ -z "$unexpected" ]] || { echo 'BLOCK: unexpected checkout paths'; exit 20; }
for path in .environment .environment.bak.* "$ids_file"; do
  [[ -e "$path" ]] || continue
  [[ $(stat -c '%a' "$path") == 600 ]] || { echo 'BLOCK: secret file mode'; exit 23; }
done
[[ $(git rev-parse --short HEAD) == "$expected_commit" ]] || { echo 'BLOCK: unexpected production commit'; exit 21; }
[[ "$batch_size" =~ ^[1-9][0-9]*$ ]] || { echo 'BLOCK: invalid batch size'; exit 22; }
load1=$(awk '{print $1}' /proc/loadavg)
cpus=$(nproc)
mem_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
blocked=$(ps -eo stat= | awk '$1 ~ /^D/ {n++} END {print n+0}')
awk -v load_value="$load1" -v cpus="$cpus" 'BEGIN {exit !(load_value <= cpus * 4)}' || { echo 'BLOCK: excessive host load'; exit 24; }
(( mem_kb >= 131072 )) || { echo 'BLOCK: insufficient available memory'; exit 25; }
(( blocked == 0 )) || { echo 'BLOCK: D-state tasks present'; exit 26; }
timeout 15 docker info --format '{{.ServerVersion}}' >/dev/null || { echo 'BLOCK: Docker API unavailable'; exit 27; }

timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/tmp/subid-legacy.out 2>&1
tail -1 /tmp/subid-legacy.out | grep -qx 'Legacy VPN audit passed.' || { echo 'BLOCK: legacy audit failed'; exit 23; }
rm -f /tmp/subid-legacy.out

total=0
while IFS= read -r record_id; do
  timeout 60 docker exec special-bot-web-1 python manage.py prepare_xui_subscriptions \
    --server-id "$server_id" --user-vpn-id "$record_id" >/dev/null
  total=$((total + 1))
done < "$ids_file"
printf 'dry_run_records=%d\n' "$total"

if [[ "$apply" != true ]]; then
  echo 'mode=dry-run changes=0'
  exit 0
fi

prepared=0
while IFS= read -r record_id; do
  timeout 90 docker exec special-bot-web-1 python manage.py prepare_xui_subscriptions \
    --apply --server-id "$server_id" --user-vpn-id "$record_id" >/dev/null
  prepared=$((prepared + 1))
  if (( prepared % batch_size == 0 )); then
    timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
    timeout 90 docker exec special-bot-web-1 python manage.py audit_xui_sub_id_coverage \
      --server-id "$server_id" >/dev/null
    printf 'batch_verified=%d\n' "$prepared"
  fi
done < "$ids_file"

timeout 90 docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
timeout 90 docker exec special-bot-web-1 python manage.py audit_xui_sub_id_coverage \
  --server-id "$server_id" >/dev/null
printf 'mode=apply prepared=%d final_audits=passed\n' "$prepared"
REMOTE
trap - EXIT
cleanup_remote
