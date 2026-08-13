# Deploying

One image, `vpnbot:latest`, runs every service. Deployment is a guarded script,
not a sequence of manual Docker commands — the script holds the preflight gates,
the migration ownership rule and the rollback.

## Before you touch a host

```bash
SPECIAL_VERIFY_PYTHON=<absolute path to the project test venv python> \
  ./ops/scripts/verify_scale_closeout.sh
```

Must be clean. The expected test and subtest count lives in `CLAUDE.md` and
nowhere else — a second copy of that number here would rot exactly as the first
one did.

### Run the suite against PostgreSQL before declaring a deploy safe

The suite runs on SQLite; production is PostgreSQL 16. The two disagree in ways
a green local run hides:

- `select_for_update()` is silently skipped on SQLite, so row-locking code is
  never actually exercised.
- Catching an `IntegrityError` inside a transaction and carrying on works on
  SQLite, but poisons the connection on PostgreSQL — every later query fails
  until rollback. The nested `transaction.atomic()` savepoint that makes this
  correct in production is invisible to the test suite.

Running the suite against a real PostgreSQL test database inside the container
has already caught a failure the local run could not. Do it as part of the
deploy, not as an optional extra: build the image, then run pytest in a
throwaway container pointed at a PostgreSQL test database on the bot host.

### The test venv must hold the versions the image installs

`requirements.txt` is what the image installs; it is compiled from
`pyproject.toml` by `uv pip compile`. A dependency left unpinned in
`pyproject.toml` therefore resolves to whatever is current whenever someone
builds a venv from it, while the image keeps the compiled pin.

That happened with `py3xui`: `requirements.txt` pins **0.5.1** and the running
container has it, but `pyproject.toml` listed it unpinned, so the project test
venv resolved to **0.7.0**. The two do not update a client the same way:

- 0.5.1 posts to `panel/api/inbounds/updateClient/{client_uuid}` — addressed by
  UUID, which is what SPECIAL clients have.
- 0.7.0 posts to `panel/api/clients/update/{email or uuid}` — a different route,
  addressed by email whenever the client carries one.

Every test touching the panel client write path was validating a request
production never sends. Two things guard it now:

- `ops/scripts/validate_repository.py` compares **every** `==` pin in
  `requirements.txt` against the versions installed in the interpreter running
  it, and names each drifted package with both versions.
  `verify_scale_closeout.sh` runs the validator under the test venv's python so
  the check sees the interpreter the suite uses. A package the interpreter does
  not have is an absence, not drift; a system interpreter is skipped entirely,
  because distro packages are nobody's pin and it never runs the suite.
- `apps/servers/test_panel_endpoint_contract.py` asserts the exact endpoint each
  client operation puts on the wire. The guard compares version strings and so
  says nothing about a deliberate upgrade of the pin; the contract tests fail on
  any library that routes differently, whichever version it claims to be.

Pin runtime dependencies in `pyproject.toml`, not only in the compiled output.

**If the guard stops you, rebuild the venv rather than pick at the packages it
names.** Drift arrives in bulk, not one package at a time: the venv measured on
2026-08-13 was 10 packages away from the image — py3xui, celery, psycopg, redis,
gunicorn, django-environ, requests and pydantic among them — because most of
`pyproject.toml` is still unpinned and a venv built from it resolves everything
fresh. Rebuilding is three commands:

```bash
uv venv <path> --python 3.13
uv pip install --python <path>/bin/python -r requirements.txt
uv pip install --python <path>/bin/python pytest pytest-django
```

The test runner is deliberately absent from `requirements.txt`: the image does
not ship it.

## The deploy itself

1. Push to `special-router/special-bot` main.
2. Run `ops/scripts/deploy_special_subscription_app.sh` from the canonical
   checkout. It connects as `specialops` and works through `sudo -n` on the
   root-owned checkout (`SPECIAL_BOT_REMOTE_PATH`, default `/root/special-bot`).

What the script does on the host, in order:

- refuses if the checkout has any modified path other than `.environment` and
  its dated backups, or if any of those is not mode `0600`;
- refuses on excessive load, under 128 MiB `MemAvailable`, any D-state task, or
  a Docker API that does not answer within 15 s;
- `git fetch` + `git merge --ff-only origin/main`, then refuses if `HEAD` is not
  the commit you said you were deploying;
- tags the current `vpnbot:latest` as a rollback image and copies `.environment`;
- runs `audit_legacy_vpn` and refuses unless it prints `Legacy VPN audit passed.`;
- `docker build -t vpnbot:latest .`;
- **migrates through one throwaway container** — `docker compose run --rm web
  python manage.py migrate`. Every long-running service has
  `RUN_MIGRATIONS=false`, so this container is the sole migration owner;
- force-recreates `web celery celery_beat monitoring`;
- proves the new image actually contains `safe_broadcast_v1`, and only then
  force-recreates `broadcast`. If the proof fails, the broadcast worker is
  stopped and removed and the deploy says `BROADCAST_QUARANTINED` — an old image
  must never consume that queue;
- runs `audit_legacy_vpn` again and asserts `SUBSCRIPTION_CONNECTOR_ENABLED`.

Any non-zero exit restores `.environment` and the previous image, purges the
`safe_broadcast_v1` queue, brings `web` up alone, waits for it to answer
`manage.py check`, and only then restores the other workers — the restored image
predates the safe broadcast task and must not race migration ownership.

## What is never restarted

PostgreSQL and Redis. They are not declared in `docker-compose.deploy.yml` at
all; that file joins the existing external `vpn_bot_default` network, and
declaring them would hijack their network aliases. They live in
`docker-compose.infrastructure.yml`, whose adoption is a separate window with
its own preflight (`preflight_special_infrastructure_adoption.sh`).

The Redis data volume is referenced by its literal external name. Replacing it
is a data migration, never an ordinary Compose recreation.

## Building the image by hand

Pull the pinned bases first, then build with `--pull=never`:

```bash
docker pull python@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6
docker pull ghcr.io/xtls/xray-core:26.6.1@sha256:16786b44020e8f4c1ff3731c73cb46fe4e1e4e07af87a0daec920e24213bfbfc
docker build --pull=never -t vpnbot:latest .
```

`--pull=never` is not cosmetic. BuildKit issues an unauthenticated `HEAD`
against the floating `python:3.13-slim` manifest on every build, and Docker Hub
answers with `429 Too Many Requests` even when the pull budget is nearly
untouched. Never respond to that `429` by unpinning a base image or by
committing a running container.

## Services and queues

| Service | Command | Queue |
|---|---|---|
| `web` | gunicorn on `:8001`, one worker, four threads, **plus** `manage.py start_bot` in the same container | — |
| `celery` | worker, `--pool=solo` | `celery` |
| `broadcast` | worker, `--pool=solo --concurrency=1` | `safe_broadcast_v1` |
| `celery_beat` | beat | — |
| `monitoring` | worker, `--pool=solo`, `no-new-privileges` | `monitoring` |

Solo pools everywhere: the prefork children were the source of the earlier OOM
pressure. The published port is pinned to the BOT public IPv4, and a persistent
`DOCKER-USER` policy permits `:8001` only from the NL nginx origin.

Beat runs two jobs unconditionally — `update_user_vpn` at 00:00 UTC and
`sync_expiry_times` at 00:05 UTC — plus the monitoring layers when their flags
are set. Those flags are read at import time, so changing them requires
restarting beat.

## Rolling back

The script's own rollback covers a failed deploy. A bad deploy discovered later
is a fresh deploy of the previous commit, through the same script, with the same
gates.
