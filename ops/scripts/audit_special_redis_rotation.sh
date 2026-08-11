#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
special_ssh_require_abs_path "$REMOTE_PATH" SPECIAL_BOT_REMOTE_PATH
OWNER_COMPOSE=${SPECIAL_REDIS_OWNER_COMPOSE_FILE:-$REMOTE_PATH/docker-compose.infrastructure.yml}
special_ssh_require_abs_path "$OWNER_COMPOSE" SPECIAL_REDIS_OWNER_COMPOSE_FILE
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "$BOT_HOST")")

"${SSH[@]}" sudo -n bash -s -- "$OWNER_COMPOSE" "$REMOTE_PATH" <<'REMOTE'
set -euo pipefail
owner_compose=$1
remote_path=$2
[[ -f "$owner_compose" ]] || { echo 'redis_owner_compose=missing'; exit 20; }
if grep -E -- '--requirepass' "$owner_compose" | grep -Evq 'REDIS_PASSWORD'; then
  echo 'redis_owner_compose=hardcoded_requirepass blocker=true'
else
  working_dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' vpn_bot-redis-1)
  if [[ $working_dir == "$remote_path" ]]; then
    echo 'redis_owner_compose=environment_driven adopted=true blocker=false'
  else
    echo 'redis_owner_compose=environment_driven adopted=false blocker=true'
  fi
fi
printf 'postgres_running='; docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1
printf 'redis_running='; docker inspect -f '{{.State.Running}}' vpn_bot-redis-1
printf 'special_app_containers='; docker ps --filter label=com.docker.compose.project=special-bot --format '{{.Names}}' | wc -l
REMOTE
