# Current status

> Snapshot date: **2026-08-08**. This is the current operational snapshot.
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

- `SUBSCRIPTION_DELIVERY_ENABLED=false`; customer-facing bot delivery is off.
- The 48-hour monitored canary soak is **waived by owner decision for schedule
  reasons**. It is recorded as skipped, never as passed; the residual risk of
  promoting without sustained observation stays with the owner.
- No mass migration or bulk `subId` assignment has occurred or is approved.
- Billing-to-`expiryTime` synchronization is not started; do not infer access
  from `enabled` alone.

See [subscription migration](SUBSCRIPTION-MIGRATION.md) and
[monitoring](MONITORING.md) for promotion gates.
