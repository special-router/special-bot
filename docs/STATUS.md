# Current status

> Snapshot date: **2026-08-10**; last guarded verification: **2026-08-10 19:53 UTC**.
> This is the current operational snapshot. `HISTORY.md` is a short
> non-authoritative chronology, not a status source.

## Live and verified

- Latest fresh-shell production verification passed against revision `a557984`:
  99 tests plus 27 subtests passed; legacy audit reported `records=65
  entitled=63 control_plane=86 control_plane_enabled=84 entitled_missing=0
  extras=23 compatibility_count=21`; Host/L0/L1/L2 monitoring states were
  healthy; bounded scale-readiness passed while
  redundancy and legacy-retirement gates remained false. External BOT `:8001`
  access was blocked and NL-to-BOT subscription routing returned the expected
  404 for a non-existent path.
- Legacy route is live: client → RU relay `:443` → NL nginx `:443` → Xray
  inbound **5**, VLESS/TCP/Reality on NL `:8443`.
- Inbound **5** is the sole active Xray listener on NL `:8443`. The previous
  same-port status/mirror listeners caused kernel distribution across unequal
  client/flow sets and intermittent Reality failures; consolidating to one
  listener restored repeated Direct and Relay protocol checks to 20/20 each.
- NL uses persistent `fq` + BBR for new TCP connections. In the same BOT-region
  1 MiB A/B sample, Direct median increased from about 29.6 to 40.3 Mbps and
  Relay from about 2.7 to 8.2 Mbps; Relay p95 transfer time fell from about
  12.0 s to 1.9 s. These are path/canary samples, not promised client ISP speed.
- RU relay admin access is verified through the protected password wrapper in
  `ops/scripts/relay_ssh.sh`. A combined relay BBR/buffer/nginx experiment
  regressed Relay to 0/8 and was rejected; automatic/manual rollback restored
  the audited `cubic` + `fq_codel`, original buffers/backlogs, 30 s stream
  timeout and no socket keepalive. Protected Direct/Relay then passed 5/5.
- Inbound **14** (`🇳🇱 NL Relay`) remains a disabled runtime/control-plane mirror
  with its client and `subId` metadata preserved. `MIRROR_INBOUND_IDS=[14]`
  continues to synchronize add/remove/enable and `subId`; the public Relay link
  still enters through `201.34.132.118:443` and terminates on active inbound 5.
- Inbound **1** (`📊 Подписка`) also remains preserved but runtime-disabled. Its
  balance-label metadata can still be synchronized, while the authoritative
  Django subscription renders the non-working status entry itself.
- Subscription delivery is **enabled** (`SUBSCRIPTION_DELIVERY_ENABLED=true`,
  `SUBSCRIPTION_CONNECTOR_ENABLED=true`). A subscription URL is the primary
  access path issued by the bot UI; the direct `vless://` key remains stored as
  fallback and rollback.
- Entitlement/control-plane snapshot: **65 Django records**, **64 currently
  entitled**, **86 primary-inbound clients**, and **21 compatibility-only**
  clients; `entitled_missing=0`. These counts are a dated live snapshot and may
  change through legitimate user activity; balance-based entitlement remains
  authoritative.
- Django stores `sub_id` for **64 of 65** `UserVPN` records: every currently
  balance-entitled record has one. The remaining record is unpaid and disabled
  in the primary 3x-ui inbound. The primary inbound has 86 unique clients:
  64 entitled, one unpaid Django-owned record, and 21 compatibility-only
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
  Direct/Relay links preserve the deployed legacy no-flow client contract;
  Vision is not forced without an explicit per-client migration. A separately
  default-off internal-inbound canary may add 801-only same-NL-origin lines
  from retained 7/9/13 TCP and 10 gRPC via public :80 after fresh per-target
  membership/enable/expiry checks. It is not an independent mirror; 8/11/12
  are excluded, :8080 is diagnostic-only, and gRPC multiMode has no URI field.
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
  `email` field. The disabled-by-default internal canary has a separate,
  fixed UserVPN 801 policy for the pre-existing retained 7/9/13/10 memberships:
  it updates enable/expiry only after exact membership validation and never
  creates or infers a target membership. Clients with no remaining days are disabled and marked
  `подписка окончена` with an `expiryTime` in the past, so happ hides them.
- `add_user` no longer stamps the telegram id/timestamp into the 3x-ui client
  `email`; working inbounds keep an empty `email` so their subscription remark
  stays clean (`🇳🇱 NL Direct` / `🇳🇱 NL Relay`).

## Production source and deployment

- Production deploy source remains the clean sudo-managed `/root/special-bot`
  checkout tracking `special-router/special-bot@main` (override with
  `SPECIAL_BOT_REMOTE_PATH`). Canonical operational tooling connects as
  `specialops` (or host override) and executes this root-owned checkout through
  `sudo -n`; direct root SSH is not an operational dependency after cutover.
  Image `vpnbot:latest` serves web, celery, celery_beat and monitoring. Verify
  the exact deployed revision with guarded scripts instead of copying a commit
  snapshot into long-lived docs.
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
- Redis ownership was moved from the dirty historical app checkout to tracked
  `docker-compose.infrastructure.yml`, then the credential was rotated across
  Redis and all app workers. The old credential is rejected; PostgreSQL was not
  restarted and the existing Redis data volume was preserved.
- BOT and NL SSH hardening completed independently with retained root
  ControlMasters and on-host timed rollback watchdogs. The locked-password
  `specialops` account uses the approved ED25519 key and isolated `NOPASSWD`
  sudo. Effective policy on both hosts is `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes` and
  `PermitRootLogin no`; fresh root SSH is rejected, fresh operator key+sudo
  succeeds, and all rollback timers/jobs were disarmed after service gates.
- Stopped legacy application containers and their unreferenced rollback images
  were retired after tracked infrastructure ownership was established. Shared
  PostgreSQL/Redis, their data volumes and compatibility clients remain live and
  were explicitly excluded from cleanup.

See [subscription migration](SUBSCRIPTION-MIGRATION.md) and
[monitoring](MONITORING.md) for promotion gates.