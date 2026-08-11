#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/special_ssh.sh"

[[ ${SPECIAL_INFRA_ADOPT_APPROVED:-false} == true ]] || {
  echo 'BLOCK: explicit SPECIAL_INFRA_ADOPT_APPROVED=true required' >&2
  exit 10
}
BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
special_ssh_require_abs_path "$REMOTE_PATH" SPECIAL_BOT_REMOTE_PATH
EXPECTED_COMMIT=${SPECIAL_INFRA_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
special_require_commit "$EXPECTED_COMMIT" SPECIAL_EXPECTED_COMMIT
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "$(special_ssh_target "$SPECIAL_BOT_SSH_USER" "$BOT_HOST")")

"${SSH[@]}" sudo -n bash -s -- "$REMOTE_PATH" "$EXPECTED_COMMIT" <<'REMOTE'
set -euo pipefail
remote_path=$1
expected_commit=$2
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

docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
docker compose -p vpn_bot --env-file .environment -f docker-compose.infrastructure.yml config >/dev/null

# Preserve the historical project/container identity (`vpn_bot-redis-1`) while
# switching its working-directory/config labels to the clean tracked owner.
# PostgreSQL is deliberately not recreated.
set -a; source .environment; set +a
export REDIS_PASSWORD
docker rm -f special-bot-redis-1 >/dev/null 2>&1 || true
docker compose -p vpn_bot --env-file .environment -f docker-compose.infrastructure.yml up -d --no-deps --force-recreate redis >/dev/null
for _ in $(seq 1 30); do
  if docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" vpn_bot-redis-1 redis-cli ping 2>/dev/null | grep -qx PONG; then break; fi
  sleep 1
done
docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" vpn_bot-redis-1 redis-cli ping 2>/dev/null | grep -qx PONG
[[ $(docker inspect -f '{{.State.StartedAt}}' vpn_bot-postgres-1) == "$postgres_started" ]]
[[ $(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' vpn_bot-redis-1) == "$redis_volume" ]]
[[ $(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' vpn_bot-redis-1) == "$remote_path" ]]
docker exec special-bot-web-1 python manage.py audit_legacy_vpn >/dev/null
docker exec special-bot-web-1 python manage.py audit_special_monitoring >/dev/null
echo infrastructure_ownership=redis_adopted postgres_unchanged=true
REMOTE
