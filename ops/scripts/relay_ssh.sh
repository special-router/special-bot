#!/usr/bin/env bash
set -euo pipefail

RELAY_HOST=${SPECIAL_RELAY_HOST:-201.34.132.118}
RELAY_USER=${SPECIAL_RELAY_USER:-root}
SECRET_FILE=${SPECIAL_RELAY_SECRET_FILE:-/home/fsdf1234/Projects/special-router-dev/.env}

[[ -r "$SECRET_FILE" ]] || { echo 'BLOCK: relay secret file unavailable' >&2; exit 20; }
command -v sshpass >/dev/null || { echo 'BLOCK: sshpass unavailable' >&2; exit 21; }

password=$(
  python3 - "$SECRET_FILE" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text(errors='ignore').splitlines():
    if line.startswith('VPN_RELAY_SSH_PASS='):
        value = line.split('=', 1)[1].strip().strip('"').strip("'")
        if value:
            print(value)
        break
PY
)
[[ -n "$password" ]] || { echo 'BLOCK: VPN_RELAY_SSH_PASS unavailable' >&2; exit 22; }
export SSHPASS="$password"
unset password

exec sshpass -e ssh \
  -o PubkeyAuthentication=no \
  -o PreferredAuthentications=password \
  -o PasswordAuthentication=yes \
  -o KbdInteractiveAuthentication=no \
  -o NumberOfPasswordPrompts=1 \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=10 \
  -o ConnectionAttempts=1 \
  "$RELAY_USER@$RELAY_HOST" "$@"
