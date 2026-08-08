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

## Explicitly not complete

- `SUBSCRIPTION_DELIVERY_ENABLED=false`; customer-facing bot delivery is off.
- Production monitoring is **NOT deployed**. Monitoring implementation commit
  `12c8d00` exists only in local `fix/legacy-stabilization-clean`.
- The required 48-hour monitored canary soak is not complete.
- No mass migration or bulk `subId` assignment has occurred or is approved.
- Billing-to-`expiryTime` synchronization is not started; do not infer access
  from `enabled` alone.

See [subscription migration](SUBSCRIPTION-MIGRATION.md) and
[monitoring](MONITORING.md) for promotion gates.
