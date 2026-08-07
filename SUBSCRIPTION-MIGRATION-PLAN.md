# Migration plan: direct VLESS keys to subscriptions

**Status:** proposed; no migration is authorized by this document.

## Invariants

- Existing `vless://` URLs, UUIDs, Reality parameters and `RELAY:443 → MAIN:443`
  path stay valid throughout the migration.
- No bulk deletion, UUID rotation, `enable` change or `expiryTime` change.
- Balance-based entitlement remains the source of truth until a separately
  reviewed billing migration is deployed.
- A subscription URL is secret bearer access data. It is sent only through the
  authenticated Telegram bot/private channel, never logged or placed in docs.
- Every client segment has a rollback to its pre-existing direct VLESS link.

## Phase 0 — prerequisites and rollback

1. Complete the staged subscription activation gates in `SUBSCRIPTION-STAGING.md`.
2. Create protected backups of the 3x-ui database/config and bot database.
3. Record a baseline using both commands:

   ```bash
   docker exec vpn_bot-django_web-1 python manage.py audit_legacy_vpn
   docker exec vpn_bot-django_web-1 python manage.py audit_xui_subscription
   ```

4. Confirm the legacy direct-key E2E path through the RU relay.
5. Define a support script/message that can resend a user's existing direct
   VLESS key without changing their UUID.

**Exit condition:** all backups, baseline audits and direct-key E2E pass.

## Phase 1 — isolated canary

1. Use one newly created internal test account, not a paying customer.
2. Assign its `subId` through the explicit connector; do not bulk backfill.
3. Fetch the subscription over public TLS; verify it contains only the canary's
   authorised client configuration.
4. Import it into Happ/V2RayN/Streisand as applicable and perform HTTPS E2E
   through `RELAY:443 → MAIN:443`.
5. Disable/delete only the canary `subId` if needed. Its direct VLESS key must
   continue working.

**Abort:** any authentication leak, wrong client contents, unavailable legacy
path, TLS/certificate error, or billing/control-plane mismatch.

## Phase 2 — voluntary pilot

1. Invite a small, explicitly consenting group (maximum 3–5 users).
2. Preserve direct VLESS delivery and offer a subscription URL as an additional
   option; do not replace the legacy key in the bot UI yet.
3. Monitor 48–72 hours: subscription fetch status, client import, E2E, support
   requests, daily `audit_legacy_vpn` invariant and listener health.
4. Keep per-user rollback: revoke only that user's subscription reference and
   resend their unchanged direct key.

**Promotion gate:** no critical incidents; all pilot users retain working
legacy access; no `entitled_missing` in daily audit.

## Phase 3 — opt-in rollout

1. Add an explicit bot action: “Get subscription URL (beta)”.
2. Require account entitlement before the connector runs.
3. Send the URL only in the private bot chat. Mask it in any admin UI/log.
4. Keep direct VLESS as the default and record voluntary adoption rate.
5. Do not couple the rollout to multi-inbound, external provider aggregation,
   a transport change, or a billing rewrite.

## Phase 4 — default for new users

Only after at least two stable billing cycles:

1. New users receive subscription URL plus a recovery path to the legacy direct
   key; existing users are not silently changed.
2. Require a separate code review before synchronising `enable`/`expiryTime`
   with 3x-ui. Test suspension and reactivation on non-production records.
3. Run a bounded user-by-user backfill, recording success and retaining each
   UUID and direct configuration.

## Phase 5 — legacy retirement decision

Direct-key retirement is a separate product/security decision, not an automatic
step. It requires measured adoption, a customer notice period, support capacity,
verified rollback exports, and explicit approval. Until then the direct VLESS
path remains supported.
