#!/usr/bin/env bash
set -euo pipefail

[[ ${SPECIAL_LEGACY_RETIRE_APPROVED:-false} == true ]] || {
  echo 'BLOCK: explicit SPECIAL_LEGACY_RETIRE_APPROVED=true required' >&2
  exit 10
}
BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "root@$BOT_HOST")

"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
containers=(vpn_bot-celery-1 vpn_bot-celery_beat-1 vpn_bot-django_web-1 vpn_bot-web-1)
images=(vpnbot:legacy-stabilization-20260807 vpnbot:legacy-stabilization-flow-20260807)

[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1) == true ]]
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-redis-1) == true ]]
docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
docker exec special-bot-web-1 python manage.py audit_special_monitoring >/dev/null
for name in "${containers[@]}"; do
  running=$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo missing)
  [[ $running == false || $running == missing ]] || { echo "BLOCK: $name is running"; exit 20; }
done
for name in "${containers[@]}"; do docker rm "$name" >/dev/null 2>&1 || true; done
for image in "${images[@]}"; do
  [[ -z $(docker ps -aq --filter ancestor="$image") ]] || { echo "BLOCK: image still referenced: $image"; exit 21; }
  docker image rm "$image" >/dev/null 2>&1 || true
done
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1) == true ]]
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-redis-1) == true ]]
docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
docker exec special-bot-web-1 python manage.py audit_special_monitoring >/dev/null
echo legacy_app_assets=retired
REMOTE
