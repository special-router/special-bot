# Subscription migration

> Canonical plan. Current state: infrastructure, monitoring and one internal
> canary are complete. The owner approved subscription-first bot delivery and a
> controlled entitled-user migration. Production delivery remains off until the
> new image, aggregate coverage audit and guarded credential rotation pass.
> The 48-hour soak was waived and must never be recorded as passed.

## Current state and invariants

The transport is live at `sub.special-wifi.ru`; the URL shape is
`https://sub.special-wifi.ru/sub/<subId>`. `<subId>` is a placeholder only and
must never be placed in documentation, logs, dashboards, or tickets.

- Existing direct `vless://` links remain live and are the rollback path.
- Keep the legacy RU relay path unchanged during this migration.
- Balance-based entitlement remains the source of truth. Current billing may
  enable/disable the existing client but must preserve `UserVPN`, UUID and
  `subId`. `expiryTime` synchronization remains a separately reviewed change.
- Subscription URLs are bearer secrets and may be delivered only privately to
  an entitled user after the relevant gate.
- `subId` preparation is allowed only for an explicit entitled `UserVPN`, one
  record per command. Dry-run and aggregate audit precede every apply batch.
- Client deletion, UUID rotation, inbound/Reality change and expiry mutation are
  not authorized. Compatibility-only clients must never be assigned ownership.
- Production currently has `SUBSCRIPTION_DELIVERY_ENABLED=false`; approved code
  changes make subscription URL the bot default only after the guarded deploy.

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

## Phase 2 — guarded production enablement (approved; pending)

1. Restore verified key-only access to the bot host and run aggregate preflight.
2. Deploy the read-only coverage command and subscription-first bot code.
3. Rotate 3x-ui admin credentials and panel path with mode-0600 backups and an
   atomic bot control-plane credential update.
4. Enable `SUBSCRIPTION_DELIVERY_ENABLED` and validate the internal canary UI
   without copying its bearer URL into logs or chat.
5. Re-run legacy entitlement, coverage and L0/L1/L2 checks. Abort and restore
   the previous image/environment/database backup on any failure.

## Phase 3 — explicit entitled-user migration

1. Obtain the eligible records from the balance-based Django entitlement query.
2. For each explicit active `UserVPN`, run dry-run, then one-record apply of
   `prepare_xui_subscriptions`; never use an unbounded apply command.
3. After each small batch, run aggregate coverage, `66/87/21`, L0/L1/L2 and
   protected subscription/direct-VLESS E2E checks.
4. The bot privately shows the stable subscription URL. Existing users must
   import it once; their installed direct profile cannot be remotely converted.
5. Insufficient balance disables the same control-plane client. Reactivation
   through the existing add-key flow reuses the same `UserVPN`, UUID and URL.

## Legacy rollback and later stages

Keep the current VLESS/Reality inbound, direct keys and all compatibility
clients operational through the entire migration. Direct keys are not displayed
when subscription delivery succeeds, but remain stored for fallback and
rollback. Their retirement requires evidence that every customer migrated plus
a separate owner approval.

`expiryTime`, additional transports, inbound removal, Django aggregation,
external providers and compatibility-client ownership are separate projects.
