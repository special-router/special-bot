# Mirror Inbounds Subscription Specification

> Spec for adding **backup external service** endpoints to the per-user
> subscription so clients can fail over to reserve VPN providers if SPECIAL
> infrastructure is down.
>
> **Definition:** "Mirror inbound" = an endpoint on an **external backup VPN
> service**, NOT our own 3x-ui ports. Our own NL ports (7,8,9,13) are
> internal relays, not mirrors — they lack per-client UUIDs and are not
> usable as subscription endpoints.

Status: design — pending provisioning of backup service access.

## Context

The per-user subscription (`apps/subscriptions/views.py::subscription_proxy`)
renders 3 lines: status + Direct (`sub.special-wifi.ru:8443`) + Relay
(`201.34.132.118:443`). If both SPECIAL endpoints fail, the client has no
fallback.

Competitor research (MORI VPN, ALL VPN, Sota VPN) shows the pattern: one
subscription, multiple providers, client switches automatically. This spec
brings the same resilience using **external backup VPN services** — not our
own unused 3x-ui ports.

## Why not our own NL ports (7,8,9,13)

Tested 2026-08-11: inbounds 7,8,9,13 on NL exist and listen, but:

- **No per-client UUIDs** — the test client UUID exists only in inbound 5
  (85 clients). Inbounds 7,8,9 have 1-2 clients each, none matching our
  users. Adding a user to inbound 5 does NOT add them to 7,8,9,13.
- `sync_expiry_times` propagates enable/expiry to `MIRROR_INBOUND_IDS`, but
  does NOT create clients — the UUIDs must be added per-inbound in 3x-ui.
- Reality SNI differs (`google.com` vs `yandex.net`), but that is not the
  blocker — the missing UUID is.
- xray probe confirmed: UUID #195 connects via inbound 5 (204 OK) but
  fails via 7,8,9,13 (000, UUID not in that inbound's client list).

**Conclusion:** our own extra ports are not mirror endpoints. They are
alternate listeners that would each need full client provisioning. Using
them as "mirrors" without per-inbound client sync would produce broken
subscription links.

## Goal

Add **external backup VPN service** endpoints to the subscription so a
client whose SPECIAL endpoint is blocked or down can switch to a reserve
provider without re-importing. Opaque provider-owned VLESS lines are rendered
as additional entries without local URI reconstruction.

## What a mirror IS (new definition)

A mirror is an **external backup VPN service** with:
- Its own host/port/protocol
- Its own client UUID (one per backup service, shared across users or
  per-user — depends on the backup provider's model)
- Its own Reality/TLS parameters
- Provisioned and maintained by an operator, not auto-discovered

## What a mirror is NOT

- Our own 3x-ui port (7,8,9,13) — those are internal, lack client UUIDs
- A relay of our own endpoint — that is the Relay line, not a mirror
- Auto-discovered from competitor subscriptions — those are research, not
  production endpoints we control

## Design

### Endpoint groups

Render order:

1. **Status** — `127.0.0.1:1` (non-working, remaining days)
2. **SPECIAL Direct** — `sub.special-wifi.ru:8443` (primary, our NL)
3. **SPECIAL Relay** — `201.34.132.118:443` (our RU relay → NL)
4. **Backup mirrors** — one VLESS line per external backup service

### Backup service configuration

Each backup subscription is configured by an operator through a mode-0600 JSON
secret file on a host path **outside this repository and Docker build context**.
For example, create `/etc/special-bot/subscription-backup.json` with mode 0600:

```json
{"upstream_urls": ["https://provider.example.invalid/opaque-subscription-SYNTHETIC"]}
```

Set only the host path and rollout controls in ignored `.environment`:

```dotenv
SUBSCRIPTION_BACKUP_SECRET_HOST_PATH=/etc/special-bot/subscription-backup.json
SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=false
SUBSCRIPTION_BACKUP_TEST_USER_IDS=[]
SUBSCRIPTION_BACKUP_UPSTREAM_HOSTS=["provider.example.invalid"]
SUBSCRIPTION_BACKUP_CONNECT_TIMEOUT_SECONDS=3
SUBSCRIPTION_BACKUP_READ_TIMEOUT_SECONDS=5
SUBSCRIPTION_BACKUP_RESPONSE_MAX_BYTES=262144
SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS=300
```

The backup secret mount is optional while the feature is disabled: Compose
binds `/dev/null` when `SUBSCRIPTION_BACKUP_SECRET_HOST_PATH` is unset. The
application rejects that nonregular device (and any missing or malformed file)
and accepts only a regular 0600 file whose JSON root is an object containing
`upstream_urls` as `list[str]`. The renderer fetches each configured
subscription within bounded time and size
limits, accepts plain newline-separated or standard-base64 payloads, filters
only coarse sentinel entries, and appends accepted `vless://` lines unchanged.
It never parses or rebuilds provider URI query parameters or fragments. Rotate
or revoke a provider bearer immediately with the provider and replace the host
secret file; never store provider lines or bearer URLs in Git.

### Feature gate

`SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED` (bool, default false). Allowlist
of `UserVPN.id` during test-group rollout, same pattern as the previous
mirror implementation.

## Prerequisites (external)

Before implementing:

1. **Provision backup VPN service(s)** — at least one external provider
   with a usable VLESS/Reality endpoint. This is an external resource, not
   derivable from current infrastructure.
2. **Credentials/UUID** for the backup service (one per user or shared —
   depends on provider model).
3. **Operator owner** for each backup service (maintenance, rotation,
   monitoring).
4. **DNS/host** — stable hostname for each backup endpoint.

## Why the previous implementation was removed

The 2026-08-11 implementation (`SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED` +
`SUBSCRIPTION_MIRROR_INBOUND_IDS=[7,8,9,13]`) was deployed to test-group
UserVPN #195 and produced 7-line subscriptions. Testing revealed all 4
mirror lines failed (xray probe: 000 for each) because the user's UUID
existed only in inbound 5, not in 7,8,9,13. The subscription rendered
correct per-inbound Reality params, but the UUID was absent from those
inbounds' client lists.

The implementation code (`_mirror_links`, `_is_mirror_test_user`) and
settings remain in the codebase as a generic mechanism, but the
`SUBSCRIPTION_MIRROR_INBOUND_IDS` must NOT point at our own ports without
per-inbound client provisioning. The settings were removed from
production `.environment` and the test subscription reverted to 3 lines.

## Safety constraints

- **No broken links in subscription** — every rendered line must be
  tested (synthetic xray probe) before going live; a broken link is worse
  than no link (client wastes time trying it).
- **Backup endpoints are external** — do not use our own 3x-ui ports as
  mirrors without full per-inbound client sync.
- **Legacy contract preserved** — flag defaults false; all existing
  subscribers see 3-line subscription until backup endpoints are
  provisioned and tested.
- **No secret exposure** — bearer URLs, provider UUIDs, raw payloads and raw
  VLESS lines remain in the host secret file, must not be logged, documented,
  or stored in Git, and are rotated/revoked through the provider.
- **Least privilege** — local Compose mounts the secret only in `django_web`
  (the HTTP service). The deploy Compose service still combines Gunicorn and
  the bot process, so it retains this residual least-privilege limitation.
- **Operator-owned** — each backup service has an accountable owner; not
  auto-discovered from competitor research.

## Open questions

- One shared UUID per backup service, or per-user UUID on the backup
  provider? Depends on provider model; shared is simpler.
- How many backup endpoints? Start with 1, add as provisioned.
- Should backup endpoints be tested automatically before inclusion? Yes —
  synthetic probe (see `docs/INBOUND-DIAGNOSTICS-SPEC.md`).
- Rotation cadence for backup endpoints? Operator decision.

## Reference

- Renderer: `apps/subscriptions/views.py::subscription_proxy`
- VLESS builder: `apps/subscriptions/views.py::_build_vless`
- Previous (removed) implementation: `_mirror_links`, `_is_mirror_test_user`
  in `apps/subscriptions/views.py` — generic mechanism, do not point at
  our own ports without per-inbound client sync.
- Settings: `bot/settings.py` `SUBSCRIPTION_MIRROR_*` (generic, currently
  unused in production).
- Diagnostics spec: `docs/INBOUND-DIAGNOSTICS-SPEC.md`