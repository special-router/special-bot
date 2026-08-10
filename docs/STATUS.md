# Current status

> Snapshot date: **2026-08-10**. This is the current operational snapshot.
> `HISTORY.md` is a short non-authoritative chronology, not a status source.

## Live and verified

- Legacy route is live: client → RU relay `:443` → NL nginx `:443` → Xray
  inbound **5**, VLESS/TCP/Reality on NL `:8443`.
- Inbound **14** (`🇳🇱 NL Relay`) mirrors inbound **5** clients with
  `externalProxy` pointing at the RU relay front `201.34.132.118:443`. It is
  kept in sync via `MIRROR_INBOUND_IDS=[14]`: add/remove/enable and `subId`
  assignment propagate from the primary inbound.
- Inbound **1** (`📊 Подписка`) is a dedicated, non-working subscription-status
  endpoint (`externalProxy` → `127.0.0.1:1`). It carries the per-client balance
  label in the 3x-ui `email` field so the subscription remark becomes
  `📊 Подписка-осталось N дней` (or `подписка окончена`). It is ordered first in
  the subscription because it has the lowest inbound id.
- Subscription delivery is **enabled** (`SUBSCRIPTION_DELIVERY_ENABLED=true`,
  `SUBSCRIPTION_CONNECTOR_ENABLED=true`). A subscription URL is the primary
  access path issued by the bot UI; the direct `vless://` key remains stored as
  fallback and rollback.
- Entitlement/control-plane snapshot: **66 entitled**, **87 control-plane**,
  **21 compatibility** clients; `entitled_missing=0`. Balance-based
  entitlement remains authoritative.
- Subscription `subId` coverage: **69** of 87 clients have a `subId`
  (all 65 entitled + canary + 3 extras). The 18 without `subId` are intentional
  compatibility-only clients that are never mutated.
- Domain subscription transport is live at `sub.special-wifi.ru`: DNS-only,
  TLS/SNI/nginx routing, and 3x-ui subscription service `:2096` at `/sub`.
  A subscription now returns three endpoints, in order:
  1. `📊 Подписка-осталось N дней` (non-working status entry, first)
  2. `🇳🇱 NL Direct` (`sub.special-wifi.ru:8443`)
  3. `🇳🇱 NL Relay` (`201.34.132.118:443`)
- Production monitoring is deployed via Celery beat plus an isolated
  `monitoring` queue. Cadence: L0 control-plane every 5 minutes, L1 regional
  TCP every minute, L2 subscription/direct-VLESS E2E every 5 minutes on the
  dedicated worker only. L0/L1 are healthy; L2 is in alert pending a canary
  recheck after the Reality/remark reconfiguration.

## Billing and subscription lifecycle

- Daily billing (`update_user_vpn`) runs at 00:00 UTC: debits the tariff price
  and disables the 3x-ui client when the remaining balance can no longer cover
  one more day.
- `sync_expiry_times` runs at 00:05 UTC (after billing) and mirrors the
  remaining balance days into the 3x-ui client `expiryTime` across the primary
  inbound and mirrors, and writes the status label into the status inbound's
  `email` field. Clients with no remaining days are disabled and marked
  `подписка окончена` with an `expiryTime` in the past, so happ hides them.
- `add_user` no longer stamps the telegram id/timestamp into the 3x-ui client
  `email`; working inbounds keep an empty `email` so their subscription remark
  stays clean (`🇳🇱 NL Direct` / `🇳🇱 NL Relay`).

## Production source and deployment

- Production checkout: `/root/special-bot` at `30e0455` (local and `origin/main`
  match). Image `vpnbot:latest` serves web, celery, celery_beat, monitoring.
- PostgreSQL and Redis are shared host containers and are not restarted by
  application deployments.

## Explicitly not complete

- The 48-hour monitored canary soak is **waived by owner decision for schedule
  reasons**. It is recorded as skipped, never as passed; the residual risk of
  promoting without sustained observation stays with the owner.
- 3x-ui admin credential/path rotation is staged but not executed; it is an
  independent hardening step that can run without disrupting subscription
  delivery.
- L2 monitoring canary is in alert after Reality/remark reconfiguration; it
  needs a recheck rather than a service change.
- OOM mitigation on the BOT host is not yet implemented: journald confirmed
  174 `celery` OOM kills on the 961 MiB / 0-swap host in the previous boot.
  Adding swap or lowering per-container memory is recommended before the next
  billing cycle to avoid a repeat.

See [subscription migration](SUBSCRIPTION-MIGRATION.md) and
[monitoring](MONITORING.md) for promotion gates.