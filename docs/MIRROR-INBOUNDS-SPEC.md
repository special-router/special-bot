# Mirror Inbounds Subscription Specification

> Spec for external backup services and the separately gated **internal inbound
> transport canary**. The internal canary is not an independent mirror or a
> country-diverse fallback: every candidate remains on the same NL origin.
> It is limited to specifically validated alternate transports.
>
> **Definition:** "Mirror inbound" = an endpoint on an **external backup VPN
> service**, NOT our own 3x-ui ports. Our own NL ports (7,8,9,13) are
> internal relays, not mirrors — they lack per-client UUIDs and are not
> usable as subscription endpoints.

**Status (2026-08-12): the ingestion code is implemented, tested and deployed
default-off.** `apps/subscriptions/views.py` fetches, classifies and renders
provider endpoints today; what is missing is a provisioned provider. The
"Design" and "Prerequisites" sections below therefore describe shipped
behaviour and an external dependency respectively, not future work. Sections
about the internal canary describe a separately gated feature that is also
implemented and off.

## Context

The per-user subscription (`apps/subscriptions/views.py::subscription_proxy`)
renders 3 lines: status + Direct (`sub.special-wifi.ru:8443`) + Relay
(`201.34.132.118:443`). If both SPECIAL endpoints fail, the client has no
fallback.

Competitor research (MORI VPN, ALL VPN, Sota VPN) shows the pattern: one
subscription, multiple providers, client switches automatically. This spec
brings the same resilience using **external backup VPN services** — not our
own unused 3x-ui ports.

## Internal inbound canary (UserVPN 801 only)

Protected operation `op-20260811T180315Z-533cbef3` later established that
UserVPN 801 exists exactly once in retained inbounds **7, 9, 13 and 10**, and
fresh RU sing-box probes passed 3/3 for each: TCP/Reality on public ports
39329, 46517 and 27914, plus gRPC/Reality through the public frontend on port
80. Inbound 10's direct `:8080` is diagnostic-only and is never advertised.

Inbound 8 and 11 failed and are absent; inbound 12 (mKCP) is unsupported and
absent. These targets are not generic mirrors: they are same-NL-origin
alternate listeners and each relies on the requested user's separate
per-inbound membership. `sync_expiry_times` does not create that membership.

The default-off runtime namespace is:

```dotenv
SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=false
SUBSCRIPTION_INTERNAL_MEMBERSHIP_SYNC_ENABLED=false
SUBSCRIPTION_INTERNAL_TEST_USER_IDS=[]
SUBSCRIPTION_INTERNAL_ENDPOINTS=[]
```

Each endpoint contains only `inbound_id`, `advertised_port`, and `label`.
The renderer accepts only 7/9/13/10 and uses one authenticated panel session
for two bounded, identical full snapshot rounds. It freshly revalidates
enabled, unexpired, exactly-once client membership for every emitted line;
uncertainty omits the complete internal batch. TCP links have empty flow. gRPC
uses the raw live `grpcSettings.serviceName` and public port 80. `multiMode`
has no VLESS URI field; its public-frontend behavior is empirical validation,
not an encoded semantic claim.

For the exactly configured UserVPN 801 canary, normal disable, reactivation,
removal and daily expiry synchronization update only pre-existing, exact UUID
memberships in all four retained targets. This separate policy never adds
these IDs to `MIRROR_INBOUND_IDS`, never creates a target member, and reports
missing, duplicate, mismatched or partial-panel results as an aggregate
fail-closed error. It makes no ownership inference.

**Superseded 2026-08-12:** this section previously said the rollout was blocked
because the production panel control-plane URL was plaintext HTTP. That blocker
is gone. The panel is HTTPS-only behind nginx at a secret base path, and
`utils/py3xui/async_api.py` now refuses anything else at construction time
(`xui_https_required`, `xui_tls_verification_required`).
`probe_special_uservpn801.py --tls-check` still fails closed and still never
prints a URL, path or certificate material. What remains blocking is an owner
decision, not a transport migration — see
[OPEN-ITEMS.md](OPEN-ITEMS.md#internal-same-origin-canary).

Protected operator backups and journals remain retained through review and
acceptance. After documented owner acceptance, an owner may use the protected
host procedure to archive/remove only the completed operation directory after
verifying its backup checksum and that no resume/manual-recovery state exists.
Never delete current production artifacts as routine cleanup.

Manual canaries must repeat entitlement, enable, expiry and exact membership
checks before use. The 48-hour soak remains waived, not passed.

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
{
  "upstream_urls": ["https://provider.example.invalid/opaque-subscription-SYNTHETIC"],
  "allowed_line_sha256": ["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]
}
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
`upstream_urls` as `list[str]`. The optional `allowed_line_sha256` must be a
`list[str]` of lowercase 64-hex SHA-256 digests of exact UTF-8 VLESS lines. If
it is absent, every protocol-validated line remains eligible; if it is present
but malformed, no external lines are accepted. The renderer fetches each
configured subscription within bounded time and size limits, accepts plain
newline-separated or standard-base64 payloads, filters only coarse sentinel
entries, then applies this digest allowlist before deduplication and appends
accepted `vless://` lines unchanged. It never parses or rebuilds provider URI
query parameters or fragments, so hashes cover their exact original bytes.
Rotate or revoke a provider bearer immediately with the provider and replace
the host secret file; never store provider lines or bearer URLs in Git.

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

An earlier internal-mirror attempt was removed after memberships were found
missing. The later protected operation above provisioned and validated only
801's retained set. Do not set `MIRROR_INBOUND_IDS` to these ports: that
setting owns synchronization behavior and is not part of this renderer.

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