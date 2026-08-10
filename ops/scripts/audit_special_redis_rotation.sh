#!/usr/bin/env bash
set -euo pipefail

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
OWNER_COMPOSE=${SPECIAL_REDIS_OWNER_COMPOSE_FILE:-/root/special-bot/docker-compose.infrastructure.yml}
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "root@$BOT_HOST")

"${SSH[@]}" bash -s -- "$OWNER_COMPOSE" <<'REMOTE'
set -euo pipefail
owner_compose=$1
[[ -f "$owner_compose" ]] || { echo 'redis_owner_compose=missing'; exit 20; }
if grep -E -- '--requirepass' "$owner_compose" | grep -Evq 'REDIS_PASSWORD'; then
  echo 'redis_owner_compose=hardcoded_requirepass blocker=true'
else
  working_dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' vpn_bot-redis-1)
  if [[ $working_dir == /root/special-bot ]]; then
    echo 'redis_owner_compose=environment_driven adopted=true blocker=false'
  else
    echo 'redis_owner_compose=environment_driven adopted=false blocker=true'
  fi
fi
printf 'postgres_running='; docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1
printf 'redis_running='; docker inspect -f '{{.State.Running}}' vpn_bot-redis-1
printf 'special_app_containers='; docker ps --filter label=com.docker.compose.project=special-bot --format '{{.Names}}' | wc -l
REMOTE
