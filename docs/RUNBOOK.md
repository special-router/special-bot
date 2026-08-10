# Safe operations runbook

This runbook describes observation and recovery gates for the live SPECIAL Bot
path. Use only this repository's `ops/scripts/` and documented Django commands;
the separate `vpn-ops` workspace belongs to another VPN service and is not an
operational source for SPECIAL Bot. Use approved access paths and environment
variables; never paste credentials, UUIDs, bearer subscription URLs, or client
configuration into terminals, tickets, or logs.

## Read-only checks

Run from the approved operations environment only.

1. Confirm aggregate entitlement/control-plane state with the existing
   read-only audit commands. Record only totals, missing/extra counts and pass/
   fail state.
2. Check service status and listeners for relay nginx, NL nginx, x-ui/Xray and
   the bot scheduler. Do not restart a service as part of a check.
3. Verify relay-to-NL reachability and subscription TLS/SNI reachability without
   requesting or printing a bearer URL.
4. Run an approved protected canary E2E only when authorized: import its
   protected configuration transiently, make an HTTPS egress check, and remove
   transient state. A successful TCP connect alone is insufficient.
5. Compare aggregate membership sets only: entitled bot records, 3x-ui DB/API,
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

Run from the canonical SPECIAL Bot checkout. Scripts are fail-closed and keep
remote secrets out of output; mutating scripts still require the authorization
stated above.

- `ops/scripts/preflight_special_subscription.sh` — read-only host/deployment
  preflight.
- `ops/scripts/deploy_special_subscription_app.sh` — guarded reproducible app
  deployment with image/environment rollback.
- `ops/scripts/backfill_special_subscription_ids.sh` — explicit mode-0600 ID
  file, dry-run by default, bounded apply batches.
- `ops/scripts/rotate_special_xui_credentials.sh` — disruptive atomic NL/BOT
  credential/path rotation; separate approval required.
- `ops/scripts/verify_special_hardening.sh` — firewall, swap, legacy and
  monitoring verification.
- `ops/scripts/verify_special_full_backlog.sh` — full local plus production
  validation; set `SPECIAL_VERIFY_PYTHON` when the repository venv is elsewhere.

For provider-console SSH recovery, follow
[TIMEWEB-BOT-SSH-RECOVERY.md](TIMEWEB-BOT-SSH-RECOVERY.md).
