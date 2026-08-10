# Subscription migration

> Canonical plan. Current state: subscription delivery is **enabled** and
> deployed. All entitled users have a `subId`; the bot UI issues subscription
> URLs as the primary path with direct `vless://` retained as fallback/rollback.
> The 48-hour soak was waived by owner decision and must never be recorded as
> passed.

## Current state and invariants

The transport is live at `sub.special-wifi.ru`; the URL shape is
`https://sub.special-wifi.ru/sub/<subId>`. `<subId>` is a placeholder only and
must never be placed in documentation, logs, dashboards, or tickets.

A subscription now returns three endpoints, ordered by inbound id:
1. `📊 Подписка-осталось N дней` (or `подписка окончена`) — non-working status
   entry, inbound id 1, `externalProxy` → `127.0.0.1:1`.
2. `🇳🇱 NL Direct` — `sub.special-wifi.ru:8443`, inbound id 5.
3. `🇳🇱 NL Relay` — `201.34.132.118:443`, inbound id 14 (mirror of 5).

- Existing direct `vless://` links remain live and are the rollback path.
- Keep the legacy RU relay path unchanged during this migration.
- Balance-based entitlement remains the source of truth. Billing
  enable/disable preserves `UserVPN`, UUID and `subId`. `expiryTime` and the
  status label are mirrored by `sync_expiry_times` from the balance daily.
- Subscription URLs are bearer secrets and may be delivered only privately to
  an entitled user after the relevant gate.
- `subId` preparation is allowed only for an explicit entitled `UserVPN`, one
  record per command. Dry-run and aggregate audit precede every apply batch.
- Client deletion, UUID rotation, inbound/Reality change are not authorized.
  Compatibility-only clients must never be assigned ownership.
- Production has `SUBSCRIPTION_DELIVERY_ENABLED=true`,
  `SUBSCRIPTION_CONNECTOR_ENABLED=true`, `MIRROR_INBOUND_IDS=[14]`,
  `STATUS_INBOUND_ID=1`.

3x-ui owns `subId` in its control plane. Its absence from generated Xray client
objects is expected projection, not membership drift.

## Completed

- DNS-only hostname, TLS, nginx SNI routing, and 3x-ui subscription listener
  `:2096` with `/sub` are live.
- Subscription-first bot UI delivered (`get_user_access_url`), deployed, and
  validated by an independent reviewer. Disabled profiles are read-only;
  reactivation reuses identity; direct VLESS stays as fallback.
- All 65 entitled users plus the canary have a `subId`. One enabled non-entitled
  record was correctly fail-closed and skipped during backfill.
- `MIRROR_INBOUND_IDS=[14]` keeps the RU relay inbound synchronized with the
  primary inbound for add/remove/enable and `subId` assignment.
- `STATUS_INBOUND_ID=1` carries the per-client balance label so happ shows the
  remaining days as a dedicated, non-working entry ordered first.
- `sync_expiry_times` runs daily after billing and mirrors `expiryTime`,
  enable state, and the status label to every relevant inbound.
- Two protected L2 subscription fetch/decode/import E2E runs passed, as did the
  canary's unchanged direct-VLESS E2E rollback.

## Remaining

- 3x-ui admin credential/path rotation is staged but not executed; it is an
  independent hardening step.
- L2 monitoring canary needs a recheck after Reality/remark reconfiguration.
- OOM mitigation on the BOT host (swap or per-container memory limits) is
  recommended before the next billing cycle.