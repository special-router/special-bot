# Architecture

## Data plane

```text
Legacy VLESS client
  → RU relay :443 (nginx stream, byte-transparent)
  → NL :443 (nginx SNI router, Reality SNI → default backend)
  → xray inbound 5 on NL :8443 (VLESS/TCP/Reality)

Subscription request
  → https://sub.special-wifi.ru/sub/<subId>
  → NL :443, SNI = sub.special-wifi.ru
  → nginx terminates TLS and proxies to BOT :8001
  → Django subscription_proxy (apps/subscriptions/views.py)
```

Both paths enter NL on `:443`. nginx dispatches by SNI: a VLESS client presents
the Reality SNI and is routed to xray; a subscription client presents
`sub.special-wifi.ru` and is routed to the subscription site, which terminates
TLS and proxies to Django on BOT `:8001`. That port is reachable only from the
NL origin, by persistent host and `DOCKER-USER` policy.

3x-ui's own subscription service is **not** on this path. It cannot be: its
plain output emits the first client UUID of an inbound for every subscriber, and
its JSON variant is not compatible with happ. Django builds the base64 VLESS
payload from the requested `UserVPN` UUID and the panel's cached Reality
parameters instead.

The gRPC/Reality inbound 10 is reachable through NL `:80`, and its backend
`:8080` is diagnostic-only and never advertised. It is part of the default-off
internal canary, not of the customer path.

## Responsibility boundaries

| Boundary | Owns |
|---|---|
| BOT host, PostgreSQL | Users, balances, entitlement, `UserVPN` UUID and persisted `sub_id`, device bindings, support tickets. The single source of truth for who may connect. |
| Django on BOT | Customer delivery. Renders the per-user subscription payload. 3x-ui subscription output is never customer-authoritative. |
| Remnawave panel on NL | Control plane since 2026-08-21. Users, squads, config profile, `shortUuid`, control-plane inventory. Reached over nginx `:8843`; the bot and the monitor read it through `vpn_client_for` / `control_plane.py`. |
| xray data plane on NL | Run by the Remnawave node, not 3x-ui. Three listeners: `:8443` VLESS/TCP/Reality, `:8080` gRPC/Reality, `:20443` XHTTP behind nginx. Both Direct and Relay terminate on `:8443`. |
| nginx on NL | TLS termination for the subscription hostname, SNI dispatch on `:443`, gRPC entry on `:80`. |
| RU relay host | Byte-transparent forwarding for the legacy entry path. Not an independent origin, and not redundancy. |
| Redis | Coordination only. Never a source of entitlement truth. |
| `docker-compose.infrastructure.yml` | Ownership definition for the shared PostgreSQL and Redis containers, their external network and data volumes. Ordinary app deployment never recreates them. |

## Daily lifecycle

- **00:00 UTC** `update_user_vpn` charges each enabled subscription the tariff
  price from the account's shared balance, oldest subscription first, and
  disables the first one the balance cannot cover along with every newer one.
  Access is switched through the panel's `status`, nothing else.
- **00:05 UTC** `sync_expiry_times` returns immediately while
  `REMNAWAVE_ENABLED` is set. It existed to mirror remaining days into 3x-ui
  `expiryTime`; the panel deliberately carries a far-future expiry so two
  clocks cannot drift into cutting off a paying customer. The remaining days a
  customer sees come from the subscription our Django renders, computed from
  the balance.

## Invariants

- **Balance-based entitlement is the source of truth.** Do not silently map it
  onto the panel's `status`; the billing task is the one place that translates.
- **Direct `vless://` delivery remains the rollback path.** Every entitled
  record keeps its direct key, and `get_user_access_url` falls back to it on any
  subscription failure.
- **Subscription URLs and client UUIDs are bearer secrets.** They belong in no
  log, document, dashboard, ticket or monitoring record.
- **`flow` stays empty everywhere — links and panel alike.** 64 of 67 deployed
  clients were issued without a Vision flow. Remnawave derives the flow when the
  field is absent (tcp+reality implies `xtls-rprx-vision`), which rejects every
  one of those links, so the config profile pins `flow: ""` explicitly.
  Promotion is a per-client migration, never a default.
- **A TCP or listener probe is reachability, not protocol health.** Only a
  protected L2 end-to-end check supports a health claim.
- **`subId` is absent from generated xray client objects.** Expected projection,
  not membership drift.

## Where the code lives

| Concern | Path |
|---|---|
| Subscription endpoint | `apps/subscriptions/views.py` |
| Subscription web page | `apps/subscriptions/page.py` |
| Link delivery to the bot | `apps/vpn/services/subscription_delivery.py`, `apps/servers/remnawave_subscription.py` |
| Device binding | `apps/subscriptions/devices.py`, `apps/subscriptions/models.py` |
| Daily billing | `apps/subscriptions/tasks.py` |
| Balance | `apps/users/querysets.py`, `apps/payments/` |
| Panel client | `apps/servers/remnawave.py`, `apps/servers/remnawave_client.py`, `apps/servers/vpn_client.py` |
| Control-plane inventory | `apps/servers/control_plane.py`, `apps/servers/remnawave_inventory.py` |
| Bot handlers and screens | `apps/telegram_bot/` |
| Monitoring layers | `apps/monitoring/` |
| Operator scripts | `ops/scripts/` |

A task-by-task index, with the flags and risks attached to each area, is in
[`CONTEXT-MAP.md`](CONTEXT-MAP.md).
