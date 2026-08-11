# Inbound Diagnostics Specification

> Spec for per-client inbound health detection without user participation.
> Status: design/ideas — not yet implemented. Return here when building the
> per-UUID diagnostic layer.

## Context

Current monitoring (L0/L1/L2) verifies the **path** is alive via a single
canary UUID. It does not verify that **each individual client UUID** can
connect. Breakages at the control-plane layer (expiry, enable, flow, pbk/sid
mismatch) are invisible to the path canary because they affect individual
clients, not the shared transport.

The 2026-08-11 incident proved this gap: 2 clients had expired `expiryTime` in
3x-ui (zero balance) and could not connect, while the path canary remained
green. `sync_expiry_times` fixed them, but the gap was found manually.

## Goal

Detect the exact failure layer for each enabled client UUID **without** the
user reporting or participating, classify the cause, and surface it as an
aggregate health signal plus a per-UUID triage table.

## Diagnostic layers (symptom → layer → check)

### 1. Client context (normally requires user — minimize this)

What we need to classify:
- Which endpoint: Direct (`sub.special-wifi.ru:8443`) or Relay
  (`201.34.132.118:443`)
- Which client app (v2rayN, happ, v2rayNG, Nekobox)
- Exact error text: `handshake failed` / `connection refused` / `timeout` /
  `authentication failed` / `i/o timeout`
- Client ISP/network
- Client UUID

**Server-side proxy:** Synthetic probes (section 3) provide the UUID and
endpoint. App-specific bugs and ISP-block remain client-side and need a
one-question follow-up, but they are the minority (~10%).

### 2. DNS layer (client-side, but checkable from representative networks)

```bash
# From client device or a probe network
nslookup sub.special-wifi.ru        # must resolve to 195.66.213.74
nslookup 201.34.132.118
```

- Wrong resolution → Cloudflare/DNS provider A-record drift
- IPv6-only client without AAAA → failure

**Server-side proxy:** Run `dig` from BOT and RU relay periodically; alert on
drift from `195.66.213.74`.

### 3. TCP layer (client → endpoint)

```bash
# Direct
timeout 6 bash -c 'cat </dev/null >/dev/tcp/195.66.213.74/8443'
# Relay
timeout 6 bash -c 'cat </dev/null >/dev/tcp/201.34.132.118/443'
```

- `connection refused` → port closed / service down (`ss -lntp` on NL)
- `timeout` → firewall / ISP block / GFW (RU clients critical)
- `route` → network unreachability

**Server-side proxy:** Synthetic probe from BOT to both endpoints (already
covered by L1). Add probe from RU relay to NL:8443 and NL:443 to detect
relay→NL degradation.

### 4. TLS/Reality handshake layer

```bash
openssl s_client -connect 195.66.213.74:8443 \
  -servername core-renderer-tiles.maps.yandex.net -tls1_3 </dev/null 2>&1 \
  | grep -E 'CONNECTED|alert|verify'
```

- `CONNECTED + Cipher TLS_AES_256` → Reality alive
- `alert handshake failure` → wrong SNI or pbk/sid mismatch (compare
  subscription vs control plane)
- `timeout` → Reality down (check `x-ui` / `xray` process on NL)

### 5. Control-plane (3x-ui) layer — most common cause

Per-UUID query:
```python
ib = await api.inbound.get_by_id(server.inbound_id)
c = next((c for c in ib.settings.clients if str(c.id) == uuid), None)
# found, enable, expiry_time, flow, sub_id
```

| Check | Cause | Fix |
|---|---|---|
| `enable=False` | Client disabled in 3x-ui | `update_user_vpn` / `sync_expiry_times` |
| `expiry_time < now` | Expired by balance | `sync_expiry_times` (recompute from balance) |
| `flow != ''` | Vision on unsupported client | Clear flow (legacy contract) |
| `client not found` | UUID missing from inbound | Check `MIRROR_INBOUND_IDS`, re-add |
| `sub_id` empty | Not generated | `ensure_subscription_reference()` / backfill |

### 6. Django (balance → enable/expiry sync) layer

```bash
docker exec special-bot-web-1 python -c "..."
# db_enabled, balance, price, days, sub_id
```

- `days <= 0` + `db_enabled=True` → `update_user_vpn` cron not run (00:00 UTC)
- `days > 0` + `cp_enable=False` → `sync_expiry_times` not run or failed
- `sub_id` empty → subscription not delivered

### 7. Subscription render layer

```bash
curl -sS -H 'Host: sub.special-wifi.ru' http://72.56.23.226:8001/sub/<sub_id> | base64 -d
```

Validate:
- Exactly 3 lines (status + Direct + Relay)
- Direct host:port = `sub.special-wifi.ru:8443`
- Relay host:port = `201.34.132.118:443`
- `sni` = `core-renderer-tiles.maps.yandex.net` (not `sub.special-wifi.ru`)
- `pbk` = CP `reality_settings.publicKey`
- `sid` ∈ CP `shortIds`
- `flow` = `''` (legacy contract)

### 8. NL SNI-router layer (relay:443 only)

```bash
sudo nginx -T 2>/dev/null | grep -A5 'map.*backend_443'
# SNI=yandex.net → default → 127.0.0.1:8443 (Xray) — correct
# SNI=sub.special-wifi.ru → 127.0.0.1:8444 (Django sub) — NOT VLESS
```

- Client sending SNI=`sub.special-wifi.ru` on `:443` → hits Django, not Xray
- VLESS clients send Reality-SNI=`yandex.net` → default → Xray (correct)

### 9. Relay nginx layer (relay path only)

```bash
# On RU relay
tail -50 /var/log/nginx/error.log | grep 'upstream timed out'
ss -lntpH | grep :443
nginx -t
```

- `upstream timed out` → relay→NL:443 channel (transient; check
  `proxy_connect_timeout`)
- `proxy_pass` must be `195.66.213.74:443` (not `:8443`)

### 10. End-to-end test (most reliable)

Run xray-client on BOT with the user's UUID, probe both paths:
```bash
docker run --rm --network host -v /tmp/cfg.json:/etc/xray/config.json:ro \
  vpnbot:latest /usr/local/bin/xray run -c /etc/xray/config.json &
curl -x socks5://127.0.0.1:10811 -o /dev/null -w '%{http_code}' \
  https://www.google.com/generate_204
```

- `204` → UUID + Reality + endpoint fully working → client-side issue (stale
  config, ISP block, app bug)
- `000` + xray log `invalid user` → UUID/enable/expiry in CP
- `000` + xray log `reality` → pbk/sid/SNI mismatch

## Symptom → layer cheat sheet

| Symptom | Likely layer | Quick check |
|---|---|---|
| `connection refused` | TCP/port | `nc -zv host port` from client |
| `timeout` | Network/ISP block | `traceroute`, alt ISP |
| `handshake failure` | Reality params | Compare pbk/sid/sni in subscription vs CP |
| `invalid user` / `auth` | CP client state | `enable`, `expiry_time`, UUID match |
| Relay works, direct broken (or vice versa) | Endpoint-specific DNS/firewall | `dig` + `nc` per endpoint |
| All clients at once | Infrastructure | NL: `systemctl x-ui`, `nginx -t`, `ss -lntp` |
| Single client | CP/client config | Layers 5-7 by UUID |

## Server-side detection without user (core of this spec)

### A. Active: per-UUID synthetic Reality probe (most powerful)

For each enabled CP client, run xray-client with their UUID on BOT, attempt
SOCKS tunnel, classify:

```
for each enabled CP-UUID:
  run xray-client with UUID → endpoint (direct 8443 / relay 443)
  curl via SOCKS → https://generate_204
  classify:
    204                       → working
    xray log "invalid user"   → UUID disabled/expired in CP (sync drift)
    xray log "reality" reject  → pbk/sid/sni mismatch (sub vs CP)
    socks timeout, no log     → TCP/ISP block on this endpoint
    xray client won't start   → broken config
```

Detects all layers 4-7 causes without the user. Limitations: does not catch
client-app bugs (stale happ, import error) or per-user ISP block — these need
"try another network" and cannot be confirmed without a dialog.

**Performance:** 85 UUID × 2 paths = 170 probes. Bounded concurrency (5
parallel xray processes, ~100ms handshake each) → ~1-2 min. Pace between
UUIDs to avoid NL rate-limit. One process per UUID = 1 xray-client, must
clean up (PID, temp config, port).

**Implementation notes:**
- Generate temp xray config per UUID with the real CP Reality params (pbk,
  sid, sni from `inbound.stream_settings.reality_settings`)
- Bind SOCKS to ephemeral loopback port, clean up after probe
- Capture xray stderr log for classification
- Run from BOT (has `vpnbot:latest` image with xray binary, network access to
  both endpoints)
- Never send real traffic through user's UUID beyond the generate_204 probe
  (no privacy concern — it's the bot's own tunnel)

### B. Passive: Xray access-log aggregation

If NL Xray access-log is enabled, each connection attempt logs UUID + result.
Filter UUIDs with `rejected` in a window but `enabled=True` in CP → sync drift.

```bash
# Check if access log is enabled
x-ui setting -show true | grep -i log
```

If `loglevel=warning` + `access=none` (common for privacy) → passive method
unavailable, fall back to active synthetic probe.

If enabled:
```
tail access.log over 1h window:
  accepted email:<uuid>  → success
  rejected email:<uuid> reason=invalid → UUID/expiry problem
  rejected reality → param mismatch
```

This is free signal — users connect themselves, logs already exist.

### C. Subscription-render audit

For each `UserVPN` with `sub_id`, fetch the rendered subscription and validate
structure against CP Reality params:

```
for each UserVPN with sub_id:
  GET /sub/<sub_id> → base64 decode
  validate:
    exactly 3 lines (status + direct + relay)
    direct host:port = sub.special-wifi.ru:8443
    relay host:port = 201.34.132.118:443
    sni = core-renderer-tiles.maps.yandex.net
    pbk = CP reality_settings.publicKey
    sid ∈ CP shortIds
    flow = '' (legacy)
```

Any drift → client's subscription is incompatible with CP → cannot connect.

### D. CP-state drift audit (extend existing)

Cross-check Django `UserVPN.enabled` / balance-derived `days` against CP
`enable` / `expiry_time` for each UUID:

```
misaligned if:
  db_enabled=True and days>0 but cp_enable=False or cp_expired
  db_enabled=False but cp_enable=True and not cp_expired
```

Run after `sync_expiry_times` and `update_user_vpn` crons to detect cron
failure. Current `audit_legacy_vpn` reports `entitled_missing` but not
per-UUID expiry/enable drift for already-enabled clients.

## Proposed monitoring layer composition

| Layer | What | Cadence | Catches |
|---|---|---|---|
| L0 host | NL resources | 1 min | capacity/conntrack/OOM |
| L1 | NL:8443 / relay:443 listeners | 1 min | service down |
| L2 canary | 1 UUID both paths | 5 min | path alive |
| **L2 per-UUID (new)** | synthetic probe all enabled | 1 hour | specific UUID broken |
| **CP-state drift (new)** | enable/expiry/flow drift DB↔CP | 1 hour | cron/sync failure |
| **sub-render audit (new)** | subscription structure vs CP | 1 hour | incompatible subscription |
| **access-log (if enabled)** | rejected UUID in window | 5 min | real connection failures |

L2 per-UUID is the only layer giving **exact cause per client without the
user**. The rest are aggregates or path-level.

## Implementation phases

### Phase 1 — per-UUID synthetic probe (highest value)

1. New management command `probe_special_inbounds` on BOT:
   - List enabled CP clients from inbound 5
   - For each UUID, generate temp xray-client config with real Reality params
   - Run xray-client (from `vpnbot:latest` image), SOCKS-probe generate_204
     on both Direct and Relay endpoints
   - Classify result (working / invalid_user / reality_reject / timeout)
   - Output aggregate counts + per-UUID triage table (UUID prefix only, no
     full UUID in logs)
2. Run as Celery task on `monitoring` queue (observational, does not restart
   services)
3. Bounded concurrency (5), pacing (1s between UUID batches), cleanup of temp
   configs/processes/ports
4. Alert if any enabled UUID fails (aggregate threshold: >0 broken enabled
   UUIDs → attention)

### Phase 2 — CP-state drift audit

1. Extend `audit_legacy_vpn` or add `audit_cp_drift`:
   - For each enabled Django UUID: check CP `enable` and `expiry_time` match
     balance-derived expectation
   - Report `drifted` count + UUID prefixes
2. Run after cron windows to detect `sync_expiry_times` / `update_user_vpn`
   failures

### Phase 3 — subscription-render audit

1. New command `audit_subscription_render`:
   - For each `UserVPN` with `sub_id`, fetch `/sub/<sub_id>`, decode, validate
     structure vs CP Reality params
   - Report incompatible renders
2. Run hourly

### Phase 4 — passive access-log (if available)

1. Check NL Xray access-log availability
2. If enabled: aggregate `rejected` events by UUID over window, correlate
   with CP `enabled=True`
3. If disabled: skip (active probe in Phase 1 covers this)

## Safety constraints

- **Observational only** — probes must not restart nginx, Xray, Docker, VPN,
  or relay services
- **No client mutation** — synthetic probes use the user's UUID for a
  read-only generate_204 request; never modify CP client state
- **No secret exposure** — logs and alerts emit UUID prefix only, never full
  UUID, Telegram ID, username, pbk, sid, or bearer paths
- **Bounded load** — concurrency and pacing must not trigger NL rate-limit or
  exhaust BOT resources (already constrained host, `--pool=solo`)
- **Aggregate-first** — monitoring output is aggregate counts; per-UUID
  triage table is available on demand, not in paging payloads
- **Legacy contract preserved** — probe config uses `flow=''` (legacy
  no-flow), never forces Vision
- **Queue isolation** — per-UUID probe runs on `monitoring` queue, not
  default Celery, so it never blocks billing/subscription tasks

## Open questions

- Should the synthetic probe run from BOT only, or also from RU relay (to
  detect relay-path-specific UUID failures)? BOT→relay:443 already exercises
  the relay path; running on BOT covers both.
- Should the probe test both Direct and Relay for each UUID, or rotate? Both
  doubles probe count (170) but gives endpoint-specific diagnosis.
- Access-log: is NL Xray access-log currently enabled? Need to check
  `x-ui setting -show` on NL before deciding Phase 4.
- Should per-UUID probe results feed into `MonitorState` (new layer
  `l3_per_uuid`) or stay as a separate command/output?

## Reference

- Canary/probe infra: `apps/monitoring/probes.py`, `apps/monitoring/tasks.py`
- CP read: `apps/servers/subscription_connector.py`,
  `utils/py3xui/async_api_inbound.py`
- Sync: `apps/subscriptions/management/commands/sync_expiry_times.py`,
  `apps/subscriptions/tasks.py`
- Subscription render: `apps/subscriptions/views.py::subscription_proxy`
- 2026-08-11 incident: 2 clients expired in CP, found via manual
  `sync_expiry_times` run; path canary stayed green throughout