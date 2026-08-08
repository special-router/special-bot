# Subscription migration

> Canonical plan. Current state: Phase 0 and one internal canary are complete.
> The 48-hour soak is **waived by owner decision for schedule reasons**; it was
> never executed and must not be recorded as passed. Migration still requires
> deployed monitoring and repeated protected L2 evidence.

## Current state and invariants

The transport is live at `sub.special-wifi.ru`; the URL shape is
`https://sub.special-wifi.ru/sub/<subId>`. `<subId>` is a placeholder only and
must never be placed in documentation, logs, dashboards, or tickets.

- Existing direct `vless://` links remain live and are the rollback path.
- Keep the legacy RU relay path unchanged during this migration.
- Balance-based entitlement remains the source of truth. `enable` and
  `expiryTime` synchronization is a separately reviewed later change.
- Subscription URLs are bearer secrets and may be delivered only privately to
  an entitled user after the relevant gate.
- No bulk `subId` assignment, client deletion, UUID rotation, `enable` change,
  or expiry change is allowed.
- `SUBSCRIPTION_DELIVERY_ENABLED=false`; legacy direct-key delivery remains the
  customer default.

3x-ui owns `subId` in its control plane. Its absence from generated Xray client
objects is expected projection, not membership drift.

## Completed prerequisites

- DNS-only hostname, TLS, nginx SNI routing, and 3x-ui subscription listener
  `:2096` with `/sub` are live.
- One explicit internal canary has a `subId`.
- Two protected L2 subscription fetch/decode/import E2E runs passed, as did the
  canary's unchanged direct-VLESS E2E rollback.

## Phase 1 — monitored canary soak (waived; not executed)

The original gate kept the internal canary under durable L0/L1/L2 observation
for 48 hours. The owner waived that duration for delivery speed. The soak was
never run, so it provides no evidence.

The reduced substitute gate is: monitoring deployed with sanitized output, plus
two protected L2 runs at least five minutes apart. Abort and preserve the direct
path on any entitled-missing count, payload leak, TLS failure, direct-key
regression, or control-plane drift.

Accepted residual risk of the waiver: slow or intermittent faults (certificate
renewal, relay flap, control-plane drift, gradual entitlement divergence) cannot
be observed in a five-minute window. Direct `vless://` remains mandatory
rollback.

## Phase 2 — voluntary pilot (not started)

Preconditions: deployed monitoring, repeated protected L2 E2E evidence,
verified control-plane membership, and explicit approval. The 48-hour soak
precondition is waived, not satisfied.

1. Invite **3–5** consenting users only.
2. Assign each `subId` individually; do not backfill a population.
3. Offer the subscription as an additional private option while preserving the
   direct VLESS key.
4. Observe 48–72 hours for payload/import/E2E health, entitlement invariant,
   support feedback and direct-link continuity.
5. Roll back per user by withholding/revoking only that subscription reference
   and resending the unchanged direct key.

## Phase 3 — opt-in delivery

Enable the delivery flag only after the pilot promotion gate. The bot must check
entitlement before private delivery, must not store or log the URL, and must
keep direct VLESS as the default. Do not couple this stage to aggregation,
additional transports, or billing rewrites.

## Later stages

After at least two stable billing cycles, separately design and test
`enable`/`expiryTime` synchronization on non-production records. Only then
consider a bounded, user-by-user new-user default. Legacy retirement, own Django
aggregation, external providers and multi-inbound subscriptions are independent
projects requiring separate approval.
