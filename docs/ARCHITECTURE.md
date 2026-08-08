# Architecture

## Current data plane

```text
Legacy VLESS client
  → $VPN_RELAY_HOST:443 (RU nginx stream relay)
  → $VPN_MAIN_HOST:443 (NL nginx SNI router)
  → Xray inbound 5 on :8443 (VLESS/TCP/Reality)

VLESS gRPC client (separate inbound)
  → NL nginx :80
  → Xray inbound 10 on :8080 (VLESS/gRPC/Reality)

Subscription request
  → https://sub.special-wifi.ru/sub/<subId>
  → $VPN_MAIN_HOST:443 (SNI=sub.special-wifi.ru)
  → 3x-ui subscription service :2096 /sub
```

`sub.special-wifi.ru` is DNS-only. Nginx performs TCP/SNI routing; it does not
terminate the Reality path. The subscription service terminates its own TLS.

## Responsibility boundaries

| Boundary | Owner / responsibility |
|---|---|
| `$BOT_HOST` / bot database | User records, balances, entitlement calculation, private delivery UX and audit scheduling. |
| 3x-ui control plane on `$VPN_MAIN_HOST` | Inbounds, client membership, `subId`, subscription rendering and control-plane inventory. |
| Xray data plane on `$VPN_MAIN_HOST` | Active VLESS transports and encrypted client traffic. Generated Xray clients intentionally do not carry the control-plane-only `subId`. |
| `$VPN_RELAY_HOST` | Byte-transparent RU relay for the legacy entry path; it is not an independent origin. |
| nginx on `$VPN_MAIN_HOST` | TCP/SNI dispatch: subscription SNI to `:2096`, legacy TLS/Reality traffic to `:8443`; gRPC entry to `:8080`. |

## Invariants

- Direct `vless://` delivery and its existing client configuration remain the
  rollback path during subscription work.
- The bot's balance-based entitlement is the current source of truth; do not
  silently map it to 3x-ui `enable` or `expiryTime`.
- Subscription URLs are bearer secrets. Documentation, logs, dashboards and
  monitoring state contain neither URLs nor identifiers.
- A listener/TCP probe is L1 reachability evidence, not VPN protocol health;
  protected L2 E2E is required for a health claim.
