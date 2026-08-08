# Migration plan: direct VLESS keys to subscriptions

**Status:** Phase 0 and one internal Phase 1 canary completed. The 3x-ui DB →
Xray projection is verified; customer migration remains paused until the
48-hour monitored canary soak completes and a pilot is explicitly approved.

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

**Completed:** protected bot/3x-ui database backups, read-only baselines and
direct VLESS E2E through the RU relay passed before canary assignment. The
3x-ui DB → Xray projection was then verified read-only: `subId` is a
subscription-service field and is intentionally absent from generated Xray
client objects; client membership is identical `87/87`.

**Remaining exit condition:** complete the 48-hour canary soak under durable
L0/L1/L2 scheduling.

## Phase 1 — isolated canary: completed, soak pending

1. ✅ One explicit internal `UserVPN` record only was assigned a `subId`.
2. ✅ Public TLS subscription fetch returned one strict-base64 VLESS entry.
3. ✅ Imported entry passed HTTPS E2E with expected NL egress.
4. ✅ The canary's unchanged direct VLESS key passed E2E after subscription test.
5. ⏳ Keep this canary under monitoring for 48 hours.

**Blocker removed:** 3x-ui API/control-plane inventory exposed `subId` values,
while the inspected active Xray runtime config did not. This is expected: the
field belongs to the subscription service, not the Xray client schema. The DB
and runtime client membership sets match `87/87`.

**Promotion gate:** keep the internal canary under 48-hour monitoring before
assigning a second user, enabling bot delivery or conducting a customer pilot.

**Abort:** any authentication leak, wrong client contents, unavailable legacy
path, TLS/certificate error, billing/control-plane mismatch, or
`entitled_missing > 0`.

## Phase 2 — voluntary pilot: not started

Preconditions: 48-hour canary soak, two repeated L2 E2E probes, verified
3x-ui DB → Xray projection, and explicit approval.

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
