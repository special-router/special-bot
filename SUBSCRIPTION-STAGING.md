# Staged 3x-ui subscriptions

## Scope of this change

This stage only prepares the bot-side connector. It does **not** activate the
3x-ui subscription listener, assign a `subId`, issue a subscription URL, alter
billing, alter legacy `vless://` keys, or change an existing 3x-ui client.

The connector remains disabled unless the deployment environment explicitly
sets:

```dotenv
SUBSCRIPTION_CONNECTOR_ENABLED=false
SUBSCRIPTION_BASE_URL=
```

The default is `false`. `SUBSCRIPTION_BASE_URL` must be a public HTTPS origin
or path with no query string or fragment, for example:

```dotenv
SUBSCRIPTION_BASE_URL=https://sub.example.org/sub
```

Do not add a production domain or enable the connector until DNS and TLS are
complete.

## What the connector does after a future explicit activation

`XUISubscriptionConnector.ensure_subscription_reference()`:

1. logs in to the configured 3x-ui server;
2. finds the existing UUID in its configured inbound;
3. assigns a random `subId` only if one is missing;
4. returns `SUBSCRIPTION_BASE_URL/<subId>`.

It does not create/delete clients, toggle `enable`, set `expiryTime`, or change
legacy key delivery. Any billing integration requires a separate reviewed
change.

## Current read-only readiness check

```bash
docker exec vpn_bot-django_web-1 python manage.py audit_xui_subscription
```

Expected while this stage is inactive:

```text
connector_enabled=False
base_url_configured=False
server_id=<id> clients=<n> enabled=<n> with_sub_id=<n> missing_sub_id=<n>
Subscription readiness audit completed (read-only).
```

The command must not be used as a migration command.

## Activation gates (not part of this change)

All must be satisfied before enabling 3x-ui subscriptions:

1. A dedicated subscription hostname has an authoritative A/AAAA record for
   the intended server.
2. A valid certificate covers that hostname and its renewal path is tested.
3. The chosen TCP port and firewall path are documented; it does not displace
   the existing legacy VLESS listener on TCP/443.
4. An allowlisted external probe confirms TLS and an unauthorized request does
   not disclose another user's subscription.
5. A single disposable canary client has a `subId`; fetch, decode and client
   import E2E pass.
6. Backup and rollback commands are recorded before any x-ui restart.
7. Existing direct `vless://` clients remain untouched.

Only after those gates may an approved operation configure 3x-ui `subEnable`,
subscription listener/certificate fields, the production base URL, and the
connector flag. The first rollout is one canary, not a bulk `subId` backfill.
