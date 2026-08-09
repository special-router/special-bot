# Current status

> Snapshot date: **2026-08-09**. This is the current operational snapshot.
> `HISTORY.md` is a short non-authoritative chronology, not a status source.

## Live and verified

- Legacy route is live: client → RU relay `:443` → NL nginx `:443` → Xray
  inbound **5**, VLESS/TCP/Reality on NL `:8443`.
- Inbound **10** is VLESS/gRPC/Reality on NL `:8080`.
- Entitlement/control-plane snapshot: **66 entitled**, **87 control-plane**,
  **21 compatibility** clients. Balance-based entitlement remains authoritative.
- Domain subscription transport is live at `sub.special-wifi.ru`: DNS-only,
  TLS/SNI/nginx routing, and 3x-ui subscription service `:2096` at `/sub`.
- One internal canary passed two protected subscription fetch/decode/import E2E
  runs and a direct-VLESS rollback check.
- **Production monitoring is deployed** on the bot host via Celery beat plus an
  isolated `monitoring` queue. Cadence: L0 control-plane every 5 minutes, L1
  regional TCP every minute, L2 subscription/direct-VLESS E2E every 5 minutes on
  the dedicated worker only. Current state: all three layers `last_ok=true`, no
  open alerts. L2 is never executed by the ordinary worker.

## Explicitly not complete

- `SUBSCRIPTION_DELIVERY_ENABLED=false`; customer-facing bot delivery is still
  off in production. Subscription-first UI code is pushed at `037b796`, tested
  with `56 passed`, and awaits guarded deployment.
- The 48-hour monitored canary soak is **waived by owner decision for schedule
  reasons**. It is recorded as skipped, never as passed; the residual risk of
  promoting without sustained observation stays with the owner.
- The owner approved controlled migration of entitled users. No population
  backfill has occurred yet: preparation remains one explicit active `UserVPN`
  per apply command, with aggregate checks between batches. Compatibility-only
  clients remain excluded.
- BOT host SSH currently accepts TCP but does not send a protocol banner from
  local or NL. Timeweb Cloud console recovery is required before deployment,
  coverage audit and atomic 3x-ui credential rotation can proceed.
- Billing-to-`expiryTime` synchronization is not started. Existing billing
  enable/disable behavior preserves the user record and client identity.

See [subscription migration](SUBSCRIPTION-MIGRATION.md) and
[monitoring](MONITORING.md) for promotion gates.
