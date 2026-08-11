# Safe operations runbook

This runbook describes observation and recovery gates for the live SPECIAL Bot
path. Use only this repository's `ops/scripts/` and documented Django commands;
the separate `vpn-ops` workspace belongs to another VPN service and is not an
operational source for SPECIAL Bot. Use approved access paths and environment
variables; never paste credentials, UUIDs, bearer subscription URLs, or client
configuration into terminals, tickets, or logs.

## RU relay administrative access

The current verified emergency access path is `root` password authentication
using `VPN_RELAY_SSH_PASS` from the protected, untracked
`/home/fsdf1234/Projects/special-router-dev/.env`. Use
`ops/scripts/relay_ssh.sh`; it passes the secret through `sshpass -e`, keeps
strict existing host-key verification, and never places the password in argv or
output. Never paste or log the value. A previous failed check came from an
incorrect value-extraction command, not credential rotation; the corrected
path authenticated and verified host `msk-1-vm-pdmp`, active nginx and four
relay listeners.

This is a temporary compatibility/admin path, not the desired end state. The
relay currently has a different historical authorized key. Migrate to a newly
verified operator key and rotate the exposed password only with a retained
provider-console/password rollback session.

## Read-only checks

Run from the approved operations environment only.

1. Confirm aggregate entitlement/control-plane state with the existing
   read-only audit commands. Record only totals, missing/extra counts and pass/
   fail state.
2. Check service status and listeners for relay nginx, NL nginx, x-ui/Xray and
   the bot scheduler. Do not restart a service as part of a check.
3. Verify relay-to-NL reachability and subscription TLS/SNI reachability without
   requesting or printing a bearer URL.
4. Run `ops/scripts/tune_special_nl_tcp.sh verify`: it checks the single NL
   `:8443` listener plus persistent `fq`/BBR without restarting services.
   For RU relay checks, use `ops/scripts/relay_ssh.sh` and verify nginx config,
   the four listeners and NL targets before any mutation.
5. Run an approved protected canary E2E only when authorized: import its
   protected configuration transiently, make an HTTPS egress check, and remove
   transient state. A successful TCP connect alone is insufficient. For
   repeated Direct/Relay fixed-file A/B samples, run
   `ops/scripts/benchmark_special_vless.py` inside the isolated monitoring
   container; never report raw links or canary identifiers.
6. Compare aggregate membership sets only: entitled bot records, 3x-ui DB/API,
   and Xray runtime. `subId` absent from generated Xray client objects is
   expected; membership drift is not.

## Recovery source of truth

1. **Entitlement:** balance relative to tariff in the bot database.
2. **Desired client identity:** entitled `UserVPN` records.
3. **Control plane:** 3x-ui DB/API inventory.
4. **Runtime:** generated Xray configuration and active listener.
5. **Compatibility:** existing runtime clients remain protected until their
   direct links are demonstrably retired.

For a legacy emergency reconciliation, the safety rule is conceptually:

```text
target membership = entitled membership ∪ existing runtime membership
```

Do not replace runtime membership with only the current bot set: that can
remove compatibility clients and strand users with old direct links.

## Mutation and rollback gates

A mutation requires explicit approval, a protected backup of the relevant
control-plane and runtime configuration, before-state aggregates, and a defined
operator/rollback owner. Change only the approved missing membership; do not
rotate client identity or alter transport parameters during recovery.

Proceed only if all post-change gates pass:

- control-plane and runtime services are active and listeners are present;
- entitled-missing count is zero;
- an approved direct legacy E2E and a compatibility E2E pass;
- no new service error condition is observed.

If any gate fails, stop further mutation. Restore the approved protected backup
using the established operations procedure, verify the legacy listener and
protected direct E2E, then investigate before retrying. Do not perform broad
cleanup, client deletion, server reboot, or automatic restart loops.

## Credential rotation gate

Credential rotation is a coordinated disruptive operation, not a read-only
check. Before starting, obtain explicit owner approval and keep an already-open
verified SSH key session on every affected host. Generate values on the target
host or in an approved secret store; never print them or put them in Git.

### 3x-ui

1. Capture a mode-0600 database backup and aggregate pre-state only.
2. Generate a new admin username, password and web base path without echoing
   values. Update the single `users` row and `webBasePath` atomically.
3. Restart `x-ui.service` once, then verify key-only SSH, panel login through
   the new path, configured inbound inventory and legacy direct-VLESS E2E. Do
   not alter client UUIDs, Reality parameters, ports or inbounds.
4. Keep the protected backup until all post-change checks pass.

### Redis and bot services

The tracked `docker-compose.infrastructure.yml` is the target clean owner for
shared PostgreSQL/Redis. Adopt it only in an approved infrastructure window and
only after verifying the external volume/network names. Until adoption, the
rotation audit must fail closed rather than use the dirty historical checkout.

1. Capture the mode-0600 bot environment backup and aggregate service state.
   PostgreSQL must not be restarted.
2. Generate a new Redis password without printing it. Update `REDIS_PASSWORD`
   and explicit `REDIS_URL`; update Redis runtime through the owning `vpn_bot`
   Compose project.
3. Stop only bot app/Celery services, restart Redis once, verify authenticated
   connectivity, then start web/Celery/beat and the isolated monitoring worker.
   Never declare duplicate `postgres` or `redis` service aliases in another
   Compose project.
4. Verify Telegram health, `audit_legacy_vpn`, L0/L1/L2, queue isolation and
   `66/87/21`; use protected backups for rollback if any gate fails.

Never combine credential rotation with subscription pilot, bulk `subId`,
expiryTime, billing, inbound deletion or transport changes.

## Guarded scripts

Run from the canonical SPECIAL Bot checkout. Canonical BOT/NL scripts connect
as the named operator `specialops` by default (`SPECIAL_SSH_USER`), with
`SPECIAL_BOT_SSH_USER` and `SPECIAL_NL_SSH_USER` available for host-specific
accounts. They require a pinned key, `BatchMode=yes`, and `sudo -n`; `/root/special-bot`
may remain the sudo-managed checkout via `SPECIAL_BOT_REMOTE_PATH`. Do not use
root SSH after the cutover. SCP inputs are staged only below `SPECIAL_SSH_TMP_DIR`
(default `/tmp`, mode-0600 artifacts) and privileged remote bodies run through
`sudo -n`. Scripts are fail-closed and keep remote secrets out of output;
mutating scripts still require the authorization stated above.

- `ops/scripts/preflight_special_subscription.sh` — read-only host/deployment
  preflight.
- `ops/scripts/deploy_special_subscription_app.sh` — guarded reproducible app
  deployment with image/environment rollback.
- `ops/scripts/backfill_special_subscription_ids.sh` — explicit mode-0600 ID
  file, dry-run by default, bounded apply batches.
- `ops/scripts/rotate_special_xui_credentials.sh` — disruptive atomic NL/BOT
  credential/path rotation; separate approval required.
- `ops/scripts/preflight_special_infrastructure_adoption.sh` — read-only checks
  that the tracked infrastructure definition references the exact live external
  network/data volumes without recreating PostgreSQL or Redis.
- `ops/scripts/adopt_special_infrastructure_ownership.sh` — explicit-window
  adoption of Redis into the clean tracked owner, preserving its data volume and
  proving PostgreSQL was not recreated. PostgreSQL ownership adoption remains a
  separate backup/restore window.
- `ops/scripts/rotate_special_redis_credentials.sh` — disruptive clean-owner
  Redis rotation; separate window required, PostgreSQL is excluded.
- `ops/scripts/harden_special_ssh.sh` — root-bootstrap-only staged SSH hardening;
  refuses without `SPECIAL_SSH_HARDEN_APPROVED=true`. Before cutover it provisions
  or verifies `specialops`, its single ED25519 key and isolated `NOPASSWD` sudoers
  entry, proves a fresh non-multiplexed key+sudo session, retains a root master,
  arms an on-host fifteen-minute rollback watchdog, verifies effective
  `sshd -T -C` precedence, reloads one host, then proves fresh `specialops` and
  rejects fresh root before disarming rollback. Run BOT fully before NL; do not
  invoke it without an approved retained root/provider recovery channel.
- `ops/scripts/retire_special_legacy_app_assets.sh` — exact allowlisted stopped
  app cleanup; refuses by default and never targets PostgreSQL/Redis.
- `ops/scripts/verify_special_hardening.sh` — firewall, swap, legacy and
  monitoring verification.
- `ops/scripts/verify_special_full_backlog.sh` — full local plus production
  validation; set `SPECIAL_VERIFY_PYTHON` when the repository venv is elsewhere.
- `ops/scripts/verify_scale_closeout.sh` — local CI-equivalent docs, origin,
  migration and full-test validation for scale-readiness changes. With
  `SPECIAL_VERIFY_PRODUCTION=true`, it also runs aggregate production hardening,
  scale-readiness and Redis ownership checks; external gates remain false.

For provider-console SSH recovery, follow
[TIMEWEB-BOT-SSH-RECOVERY.md](TIMEWEB-BOT-SSH-RECOVERY.md).
