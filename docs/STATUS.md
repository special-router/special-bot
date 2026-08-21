# Current status

> Reviewed **2026-08-21** after the Remnawave cutover.
> [`HISTORY.md`](HISTORY.md) is a chronology, not a status source.

Two kinds of fact live here and they are labelled, because mixing them is what
made the previous version of this document contradict itself.

- **Repo-verifiable** — checkable by reading this repository. Trust it, and fix
  it when it drifts.
- **Operator-reported** — the state of running hosts and database rows. The
  repository has no way to confirm any of it. Treat it as dated, and re-check
  before acting.

## Repo-verifiable

- The local verifier passes: **296 tests, 110 subtests**, plus repository
  validation (including flag-documentation drift), origin-schema validation,
  shell syntax and `makemigrations --check`.
- Delivery, the connector, monitoring, backup ingestion and the internal canary
  are all **default-off in code**. Their production values are a separate matter
  — see [`FLAGS.md`](FLAGS.md).
- `SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED` defaults to **`True`** in
  `bot/settings.py`, while production runs phase 1 with it set to `false`. A
  fresh deployment that forgets the override lands in phase 2 by accident.
- The 3x-ui client refuses non-HTTPS and refuses to skip TLS verification:
  `xui_https_required`, `xui_tls_verification_required` in
  `utils/py3xui/async_api.py`. There is no plaintext path from this code.
- Billing is idempotent per subscription per UTC day through a partial unique
  constraint on `(user_vpn, charge_date)` where `source='EVERYDAY_SYSTEM'`, with
  a five-minute early-start tolerance on the date key.
- One image serves everything. `web` runs gunicorn *and* the bot process in the
  same container; all Celery workers use `--pool=solo`.
- PostgreSQL and Redis are absent from `docker-compose.deploy.yml` by design, so
  no application deploy can restart them.
- Support tickets and premium button icons are implemented, tested and inert.

## Operator-reported, as of 2026-08-21

### Live path

- **The control plane and data plane are Remnawave since 2026-08-21.** 3x-ui is
  stopped and `systemctl disable`d on NL; the Remnawave node owns `:8443` (TCP
  Reality), `:8080` (gRPC Reality) and `:20443` (XHTTP). See
  [`REMNAWAVE-MIGRATION.md`](REMNAWAVE-MIGRATION.md).
- Legacy route unchanged for the customer: client → RU relay `:443` → NL nginx
  `:443` → xray on NL `:8443`. Client links and `sub_id` values were preserved,
  so nothing was reissued.
- Subscriptions are still rendered by our Django, not by the panel. The live
  payload carries 14 lines, nine of which are mirror-provider countries the
  panel knows nothing about; proxying `/sub/` to it would delete them.
- Subscription transport is live on `sub.special-wifi.ru`. NL nginx terminates
  TLS and proxies `/sub/` to Django on BOT `:8001`, which is reachable only from
  the NL origin. The panel is served by the same nginx on `:8843`.
- Delivery is enabled: the bot issues subscription URLs as the primary path,
  with the direct `vless://` key retained as fallback and rollback.
- nginx `worker_connections` is 16384 with `worker_rlimit_nofile 65535`. The
  Debian default of 768 was exhausted within minutes of the cutover, because
  every Reality connection now traverses the stream proxy.
- Nine clients that live in the panel but not in the bot database were imported
  by hand as `legacy_*`: two grpc routers carrying real traffic and seven xhttp
  clients. Without them the cutover would have cut them off.
- NL uses persistent `fq` + BBR. In a bot-region 1 MiB A/B sample, Direct median
  went from ~29.6 to ~40.3 Mbps and Relay from ~2.7 to ~8.2 Mbps, with Relay p95
  transfer time falling from ~12.0 s to ~1.9 s. Path samples, not promised
  client speed.
- A combined relay BBR/buffer/nginx experiment regressed Relay to 0/8 and was
  rolled back to the audited `cubic` + `fq_codel`. Future relay tuning changes
  and benchmarks one knob at a time.
- L0, L1, L2 and Host monitoring run on schedule, with L2 and Host confined to
  an isolated `monitoring` queue and worker. External paging is deployed and
  off.

### Entitlement snapshot

The L0 control-plane probe, run against the panel right after the cutover on
**2026-08-21**:

```text
records=46 entitled=42 control_plane=55 control_plane_enabled=52
entitled_missing=0 extras=13
```

`entitled_missing=0` is the gate that matters: nobody who paid is without
access. `extras=13` is the nine hand-imported `legacy_*` clients plus four bot
users whose balance is below the tariff. The other counts move with ordinary
user activity — do not quote them as current. Re-run the probe instead:

```bash
docker exec special-bot-web-1 python manage.py shell -c \
  "from apps.monitoring.tasks import run_control_plane_monitor; print(run_control_plane_monitor())"
```

### Hosts

- BOT: persistent 1 GiB `/swapfile`, UFW active, `:8001` published on the public
  IPv4 only and firewalled to the NL origin.
- NL: externally reachable on 22, 80, 443, 2096, 3000, 8080, 8443, 27914, plus
  8843 for the Remnawave panel. **The iptables rules are not persistent across a
  reboot.** See [`OPEN-ITEMS.md`](OPEN-ITEMS.md#reducing-the-nl-host-to-2280443).
- NL now has a persistent 2 GiB `/swapfile` in `/etc/fstab`. It had none, and
  the panel took 396 MiB of it within the hour.
- SSH on both hosts: `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`,
  `PermitRootLogin no`. The locked-password `specialops` account with an ED25519
  key and isolated `NOPASSWD` sudo is the operational path. Root SSH is
  rejected. The RU relay is the exception and still uses a password.
- 3x-ui admin credentials and the panel base path were rotated atomically across
  NL and BOT with protected rollback. A second rotation followed immediately
  because the library exposed the first generated username in an `INFO` log;
  `py3xui` is now pinned to `WARNING`.
- Redis ownership moved to the tracked infrastructure Compose file and its
  credential was rotated; the old one is rejected, PostgreSQL was not restarted,
  and the data volume was preserved.
- Stopped legacy application containers and their unreferenced rollback images
  were retired. Shared PostgreSQL/Redis, their volumes and compatibility clients
  were explicitly excluded.

## Not done

Open work, with its blockers, is in [`OPEN-ITEMS.md`](OPEN-ITEMS.md). The
headline items: the NL port reduction, device-binding phase 2, support tickets,
premium icons, external paging, and a second independent origin.
