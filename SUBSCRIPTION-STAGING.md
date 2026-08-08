# Staged 3x-ui subscriptions

## Current domain-backed infrastructure

The documented domain plane is ready for a later canary:

- hostname: `sub.special-wifi.ru`;
- DNS-only A record: NL MAIN;
- public Let's Encrypt certificate on NL;
- nginx SNI stream on `:443`: subscription SNI → 3x-ui `:2096`, other SNI → Xray legacy inbound on `:8443`;
- 3x-ui subscription listener: `:2096` with `/sub/`.

The existing customer path remains separate: RU relay `:443` → NL nginx `:443` →
Xray legacy inbound. No existing customer UUID or direct VLESS key changed during
this work. One internal canary was assigned a `subId` and passed subscription
fetch/decode/import E2E plus direct-key rollback.

## Scope of this change

This stage prepares the bot-side connector and migration tooling. Bot
subscription delivery remains disabled and billing, legacy `vless://` keys, and
existing customer clients are unchanged.

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
  `--apply` and `SUBSCRIPTION_CONNECTOR_ENABLED=true`, plus a specific
  `--server-id` and `--user-vpn-id`; it cannot bulk backfill clients.

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

The first internal canary already passed. Do not use the command for another
record until the 48-hour canary soak completes and pilot approval is explicit.

## Remaining promotion gates

Before enabling bot delivery or preparing a second user:

1. The 3x-ui DB → Xray projection is documented and membership is verified
   identical (`87/87`); no x-ui restart is required for `subId`.
2. Repeat protected canary E2E twice, at least five minutes apart, while the
   direct VLESS rollback path remains healthy.
3. Complete the 48-hour canary soak with `audit_legacy_vpn`,
   `audit_xui_inbounds` and regional probes.
4. Obtain explicit approval before any pilot assignment or bot handler
   enablement.
