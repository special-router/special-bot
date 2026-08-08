# Staged 3x-ui subscriptions

## Current domain-backed infrastructure

The documented domain plane is ready for a later canary:

- hostname: `sub.special-wifi.ru`;
- DNS-only A record: NL MAIN;
- public Let's Encrypt certificate on NL;
- nginx SNI stream on `:443`: subscription SNI → 3x-ui `:2096`, other SNI → Xray legacy inbound on `:8443`;
- 3x-ui subscription listener: `:2096` with `/sub/`.

The existing customer path remains separate: RU relay `:443` → NL nginx `:443` →
Xray legacy inbound. No existing client or UUID is changed by this staging work.

## Scope of this change

This stage prepares the bot-side connector and migration tooling. It does **not**
activate bot subscription delivery, assign production `subId` values, alter
billing, alter legacy `vless://` keys, or change an existing 3x-ui client.

The connector remains disabled unless the deployment environment explicitly sets:

```dotenv
SUBSCRIPTION_CONNECTOR_ENABLED=false
SUBSCRIPTION_BASE_URL=https://sub.special-wifi.ru/sub
```

`SUBSCRIPTION_BASE_URL` also accepts the existing `SUB_URL` setting as a default,
but the explicit connector flag remains false until a canary is approved.

## Bot migration layer

- `get_subscription_url(user_vpn)` is read-only and requires an existing 3x-ui
  `subId`.
- `prepare_subscription_url(user_vpn)` can assign a missing `subId`, but only
  when the connector flag is enabled.
- Neither helper is imported by legacy Telegram handlers or billing.
- `prepare_xui_subscriptions` is dry-run by default. Mutation requires both
  `--apply` and `SUBSCRIPTION_CONNECTOR_ENABLED=true`, and `--limit=1..5` is
  mandatory to keep the first operation a bounded canary.

Commands:

```bash
# Read-only; safe while connector is disabled.
docker exec vpn_bot-django_web-1 python manage.py audit_xui_subscription

# Read-only candidate count; no 3x-ui update.
docker exec vpn_bot-django_web-1 python manage.py prepare_xui_subscriptions --server-id 1

# Future internal canary only, after explicit activation approval.
docker exec vpn_bot-django_web-1 python manage.py prepare_xui_subscriptions \
  --server-id 1 --user-vpn-id <internal-canary-record> --apply
```

The last command is intentionally not run in this change.

## Activation and E2E gates

Before enabling the connector flag:

1. Verify DNS, certificate expiry/renewal and nginx/x-ui listeners.
2. Back up 3x-ui DB/config and bot DB.
3. Run `audit_legacy_vpn` and record a direct VLESS E2E baseline.
4. Prepare one internal canary only.
5. Fetch `https://sub.special-wifi.ru/sub/<subId>` externally; verify HTTP 200,
   valid base64 and exactly the canary's authorised configuration.
6. Import it into the target client and test HTTPS through the RU relay.
7. Confirm the original direct VLESS key still works.
8. Roll back by disabling only the canary subscription reference if any gate fails.

No billing/`expiryTime` synchronization, user-facing bot button, bulk backfill,
external provider aggregation, or legacy-key retirement is part of this stage.
