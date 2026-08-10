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
- Entitlement/control-plane snapshot: **66 Django records**, **65 currently
  entitled**, **87 primary-inbound clients**, and **21 compatibility-only**
  clients; `entitled_missing=0`. Balance-based entitlement remains authoritative.
- Django stores `sub_id` for **65 of 66** `UserVPN` records: every currently
  balance-entitled record has one. The remaining record is unpaid and disabled
  in the primary 3x-ui inbound. The primary inbound has 87 unique clients:
  65 entitled, one unpaid Django-owned record, and 21 compatibility-only
  clients. Compatibility-only clients are never assigned ownership or mutated.
- Domain subscription transport is live at `sub.special-wifi.ru`. NL nginx
  terminates TLS for `/sub/<subId>` and proxies only from NL to the custom
  Django subscription endpoint on BOT `:8001`. This avoids 3x-ui plain
  subscription behavior that otherwise emits the first inbound client's UUID
  for every subscriber. A subscription now returns three per-user endpoints,
  in order:
  1. `📊 Подписка-осталось N дней` (non-working status entry, first)
  2. `🇳🇱 NL Direct` (`sub.special-wifi.ru:8443`)
  3. `🇳🇱 NL Relay` (`201.34.132.118:443`)
- Production runs L0 control-plane, L1 regional TCP, protected L2
  subscription/direct-VLESS and Host capacity monitoring on an isolated queue.
  Host records aggregate memory, swap, load-per-CPU and OOM count only. The
  provider-neutral paging adapter is deployed but default-off; do not describe
  external paging as live until a destination and accountable owner exist.

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

- Production deploy source is the clean `/root/special-bot` checkout tracking
  `special-router/special-bot@main`. Image `vpnbot:latest` serves web, celery,
  celery_beat and monitoring. Verify the exact deployed revision with the
  guarded scripts instead of copying a commit snapshot into long-lived docs.
- Gunicorn serves the subscription endpoint with one worker/four threads; both
  Celery workers use `--pool=solo`. This removed the prefork child processes
  that caused the prior OOM pressure.
- BOT has a persistent 1 GiB `/swapfile`. Host UFW is active, and a persistent
  `DOCKER-USER` policy permits published `:8001` traffic only from the NL nginx
  origin; direct external access is denied. The Compose port bind is pinned to
  the BOT public IPv4 instead of all IPv4/IPv6 interfaces.
- PostgreSQL and Redis are shared host containers and are not restarted by
  application deployments.

## Explicitly not complete

- The 48-hour monitored canary soak is **waived by owner decision for schedule
  reasons**. It is recorded as skipped, never as passed; the residual risk of
  promoting without sustained observation stays with the owner.
- 3x-ui admin credentials and panel path were rotated atomically across NL and
  BOT with protected rollback. A second rotation immediately invalidated the
  first generated username after the library exposed it in an INFO log; the
  `py3xui` logger is now suppressed at WARNING and no password/path was logged.
- Redis credential rotation requires a separate coordinated app/Celery stop,
  Redis restart, and rollback window. PostgreSQL must not be restarted.
- SSH password/root hardening remains deferred until a retained rollback
  session and an independently verified key-only path are available.
- Stopped legacy app containers and their rollback images remain intentionally
  preserved pending explicit owner approval. Shared PostgreSQL and Redis are
  live dependencies and are not legacy-cleanup targets.

See [subscription migration](SUBSCRIPTION-MIGRATION.md) and
[monitoring](MONITORING.md) for promotion gates.