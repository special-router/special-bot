# Mirror Inbounds Subscription Specification

> Spec for exposing existing mirror 3x-ui inbounds in the per-user
> subscription so clients can switch between endpoints without re-importing.
> Status: design — implementation pending approval for test-group rollout.

## Context

The SPECIAL NL 3x-ui panel has 8 active inbounds, but the subscription proxy
(`apps/subscriptions/views.py::subscription_proxy`) renders only 3 lines:
status + Direct (`:8443`) + Relay (`201.34.132.118:443`). The other 6
inbounds exist in the control plane and have the same client pool (UUIDs),
but are invisible to subscribers.

Competitor research (MORI VPN, ALL VPN, Sota VPN) confirms the industry
pattern: one UUID, multiple endpoints in a single subscription, client
switches automatically. This spec brings SPECIAL to the same model using
**existing** inbounds — no new production inbounds are introduced.

## Current state (2026-08-11)

| ID | Port | Protocol | Network | Security | In subscription? |
|----|------|----------|---------|----------|------------------|
| 5  | 8443 | vless    | tcp     | reality  | ✅ Direct + Relay |
| 7  | 39329| vless    | tcp     | reality  | ❌ |
| 8  | 20057| vless    | tcp     | reality  | ❌ |
| 9  | 46517| vless    | tcp     | reality  | ❌ |
| 10 | 8080 | vless    | grpc    | reality  | ❌ |
| 11 | 22554| vless    | ws      | none     | ❌ |
| 12 | 34007| vless    | kcp     | none     | ❌ |
| 13 | 27914| vless    | tcp     | reality  | ❌ |

`MIRROR_INBOUND_IDS` in `bot/settings.py` is already defined (used by
`sync_expiry_times` to propagate enable/expiry to mirror inbounds). The
subscription renderer does not read it.

## Goal

Expose selected existing mirror inbounds in the per-user subscription as
additional VLESS links, so clients get multiple endpoint choices and can
fail over automatically. Roll out to a test group first, then all entitled
users.

## Design

### Endpoint groups

Render order (clients pick best automatically):

1. **Status** — `127.0.0.1:1` (non-working, shows remaining days)
2. **Direct NL primary** — `sub.special-wifi.ru:8443` (inbound 5, existing)
3. **Direct NL mirrors** — `sub.special-wifi.ru:<port>` for each mirror
   inbound (Reality/TCP first phase)
4. **RU Relay primary** — `201.34.132.118:443` (existing, relays to NL:443
   → SNI-router → Xray:8443)

### Phases

**Phase 1 (this implementation) — Reality/TCP mirrors:**
- Inbounds 5, 7, 8, 9, 13 (all `vless/tcp/reality`)
- Same transport/security as primary — minimal client-compatibility risk
- Direct path only (relay path for non-443 mirrors requires relay nginx
  changes, deferred)
- Feature-gated to a test group of entitled `UserVPN` records

**Phase 2 (deferred) — gRPC:**
- Inbound 10 (`vless/grpc/reality`) — different transport, bypasses
  port-based blocking
- Requires client gRPC support; mark remark clearly

**Phase 3 (deferred) — relay path for mirrors:**
- Extend RU relay nginx `proxy_pass` for mirror ports, or multi-SNI on
  `:443`
- Enables Relay variants of mirror inbounds

**Not included:**
- Inbounds 11 (ws/none), 12 (kcp/none) — different security profile
  (no Reality), separate product concern, do not mix with main
  subscription

### Per-inbound Reality parameters

Reality `publicKey`, `serverName`, `shortIds` are per-inbound in 3x-ui.
The renderer must fetch params for each mirror inbound individually (they
may differ). The existing `_reality_params` lru_cache already keys by
`(server_id, inbound_id)` — extend to iterate over the mirror set.

### Transport type in VLESS link

`_build_vless` currently hardcodes `type=tcp`. Mirror inbounds with
`network=grpc` or `network=ws` need `type=grpc` / `type=ws` and different
query parameters (`serviceName` for grpc, `path`/`host` for ws). Phase 1
mirrors are all `tcp`, so this is deferred to Phase 2, but the params dict
should carry `network` now to avoid a second refactor.

### Feature gate — test group

Controlled by `SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED` (bool, default false)
and an explicit allowlist of `UserVPN` IDs that receive mirror links.
Production-wide rollout flips the flag after test-group validation.

This preserves the legacy contract for all existing clients until the
test group confirms multi-endpoint subscriptions work across happ,
v2rayNG, Nekobox.

### Remark naming

Per-inbound remarks so clients can distinguish:
- `🇳🇱 NL Direct` (primary 8443)
- `🇳🇱 NL Mirror 39329`
- `🇳🇱 NL Mirror 20057`
- `🇳🇱 NL Mirror 46517`
- `🇳🇱 NL Mirror 27914`
- `🇳🇱 NL Relay` (primary relay)

## Configuration

New settings (in `bot/settings.py`, names-only in `.env.example`):

```python
# Expose mirror inbounds in the subscription payload.
SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED = env.bool(
    'SUBSCRIPTION_MIRROR_INBOUNDS_ENABLED', False)
# Explicit allowlist of UserVPN ids that receive mirror links during
# test-group rollout. Empty = no one (even if flag is true).
SUBSCRIPTION_MIRROR_TEST_USER_IDS = env.json(
    'SUBSCRIPTION_MIRROR_TEST_USER_IDS', default=[])
# Mirror inbound ids to render (must be Reality/TCP for Phase 1).
# Defaults to the existing MIRROR_INBOUND_IDS if set, otherwise empty.
SUBSCRIPTION_MIRROR_INBOUND_IDS = env.json(
    'SUBSCRIPTION_MIRROR_INBOUND_IDS', default=[])
```

`MIRROR_INBOUND_IDS` (existing) continues to drive expiry/enable sync;
`SUBSCRIPTION_MIRROR_INBOUND_IDS` drives rendering and can be a subset
during testing.

## Validation contract

- Legacy 3-line subscription unchanged for non-test-group users (flag off
  or UserVPN not in allowlist)
- Test-group users receive status + direct primary + mirror directs + relay
- Each mirror link uses that inbound's real Reality params (pbk/sid/sni from
  CP, not the primary's)
- `flow=''` preserved (legacy no-flow contract)
- Relay link unchanged (primary relay only in Phase 1)
- Subscription still returns 404 for disabled `UserVPN`
- Tests cover: legacy path, test-group multi-link, mirror params isolation,
  empty mirror set, non-test-group exclusion

## Safety constraints

- **No new production inbounds** — only existing inbounds 7,8,9,13 are
  rendered; nothing is created in 3x-ui
- **No client mutation** — rendering is read-only; CP client state is not
  touched
- **Legacy contract preserved** — flag defaults false; all existing
  subscribers see the current 3-line subscription until rollout
- **Compatibility identities untouched** — mirror inbounds use the same
  UUID pool; 21 compatibility-only identities are not inferred or assigned
- **Direct path only for mirrors in Phase 1** — relay nginx not modified;
  relay link remains the primary `:443` path
- **No secret exposure** — subscription output is base64 VLESS links with
  the user's own UUID; pbk/sid are per-inbound Reality public params (not
  private keys)

## Open questions

- Should mirror links be ordered by port, by a priority field, or
  randomized? Default: by inbound id ascending for determinism.
- Should the test-group allowlist be `UserVPN.id` or `UserVPN.sub_id`? `id`
  is stable and not exposed in URLs.
- After test-group validation, is the rollout flag-flip or gradual
  allowlist growth? Recommend flag-flip once test group passes.
- Should `SUBSCRIPTION_MIRROR_INBOUND_IDS` include gRPC (10) in Phase 1 or
  wait for Phase 2? Wait — different transport type needs `_build_vless`
  extension.

## Reference

- Renderer: `apps/subscriptions/views.py::subscription_proxy`
- VLESS builder: `apps/subscriptions/views.py::_build_vless`
- Reality params cache: `apps/subscriptions/views.py::_reality_params`
- Mirror sync: `apps/subscriptions/management/commands/sync_expiry_times.py`
- Settings: `bot/settings.py` `MIRROR_INBOUND_IDS`, `SUBSCRIPTION_BASE_URL`
- Tests: `apps/subscriptions/test_views.py`
- Diagnostics spec: `docs/INBOUND-DIAGNOSTICS-SPEC.md`