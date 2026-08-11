#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
special_ssh_require_abs_path "$REMOTE_PATH" SPECIAL_BOT_REMOTE_PATH
COMPOSE_FILE=${SPECIAL_INFRA_COMPOSE_FILE:-docker-compose.infrastructure.yml}
special_ssh_require_relative_path "$COMPOSE_FILE" SPECIAL_INFRA_COMPOSE_FILE
EXPECTED_COMMIT=${SPECIAL_INFRA_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
special_require_commit "$EXPECTED_COMMIT" SPECIAL_EXPECTED_COMMIT
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "$BOT_HOST")")

"${SSH[@]}" sudo -n bash -s -- "$REMOTE_PATH" "$COMPOSE_FILE" "$EXPECTED_COMMIT" <<'REMOTE'
set -euo pipefail
remote_path=$1
compose_file=$2
expected_commit=$3
cd "$remote_path"
[[ $(git rev-parse --short HEAD) == "$expected_commit" ]] || { echo 'BLOCK: unexpected production commit'; exit 20; }
[[ -f .environment && $(stat -c '%a' .environment) == 600 ]] || { echo 'BLOCK: environment mode'; exit 21; }
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-postgres-1) == true ]] || { echo 'BLOCK: PostgreSQL not running'; exit 22; }
[[ $(docker inspect -f '{{.State.Running}}' vpn_bot-redis-1) == true ]] || { echo 'BLOCK: Redis not running'; exit 23; }
postgres_started=$(docker inspect -f '{{.State.StartedAt}}' vpn_bot-postgres-1)
redis_started=$(docker inspect -f '{{.State.StartedAt}}' vpn_bot-redis-1)
pg_volume=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' vpn_bot-postgres-1)
redis_volume=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' vpn_bot-redis-1)
[[ $pg_volume == vpn_bot_pgdata ]] || { echo 'BLOCK: unexpected PostgreSQL volume'; exit 24; }
[[ $redis_volume == 30e0d25e3770503c6f3fb6242f9dfdd5f542a3ffd5b2fe1c950f455540033d92 ]] || { echo 'BLOCK: unexpected Redis volume'; exit 25; }
# Read-only preflight: validate the tracked ownership definition without
# recreating or relabeling either live service.
docker compose --env-file .environment -f "$compose_file" config >/dev/null
[[ $(docker inspect -f '{{.State.StartedAt}}' vpn_bot-postgres-1) == "$postgres_started" ]]
[[ $(docker inspect -f '{{.State.StartedAt}}' vpn_bot-redis-1) == "$redis_started" ]]
echo infrastructure_adoption_preflight=passed
REMOTE
