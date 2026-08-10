#!/usr/bin/env bash
set -euo pipefail

[[ ${SPECIAL_INFRA_ADOPT_APPROVED:-false} == true ]] || {
  echo 'BLOCK: explicit SPECIAL_INFRA_ADOPT_APPROVED=true required' >&2
  exit 10
}
BOT_HOST=${SPECIAL_BOT_HOST:-72.56.23.226}
SSH_KEY=${SPECIAL_BOT_SSH_KEY:-$HOME/.ssh/id_ed25519}
REMOTE_PATH=${SPECIAL_BOT_REMOTE_PATH:-/root/special-bot}
EXPECTED_COMMIT=${SPECIAL_INFRA_COMMIT:-$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short HEAD)}
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "root@$BOT_HOST")

"${SSH[@]}" bash -s -- "$REMOTE_PATH" "$EXPECTED_COMMIT" <<'REMOTE'
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
docker compose --env-file .environment -f docker-compose.infrastructure.yml config >/dev/null

# Compose project/name remain vpn_bot, so adoption is performed one service at a
# time. PostgreSQL is deliberately not recreated. First adopt only Redis using
# its current credential; subsequent credential rotation uses the clean owner.
set -a; source .environment; set +a
export REDIS_PASSWORD
docker compose --env-file .environment -f docker-compose.infrastructure.yml up -d --no-deps --force-recreate redis >/dev/null
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
