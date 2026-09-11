# Provider integration

This is the infrastructure-only path for adding an external VPN source. A
provider is not production-ready merely because its subscription URL parses.

## Secret manifest

Store the runtime document outside Git as a regular mode-0600 file mounted at
`PROVIDER_SOURCE_SECRET_FILE`. Only the isolated provider-ingestion process
reads it. The public config endpoint, Telegram bot and monitoring process must
not receive this file:

```json
{
  "providers": [
    {
      "id": "a-service",
      "adapter": "a-service",
      "kind": "subscription",
      "url": "https://a-service.example/sub/<secret>",
      "enabled": false
    },
    {
      "id": "vpnstar",
      "adapter": "vpnstar",
      "kind": "connection_link",
      "url": "https://vpnstar.example/api/cabinet/subscription/connection-link",
      "follow_hosts": ["vpnstar-subscriptions.example"],
      "authorization": "Bearer <api-token>",
      "enabled": false
    }
  ]
}
```

- `id` is a stable lowercase slug and must match the `Provider.slug` row.
- `a-service` reads a direct plain or base64 VLESS subscription.
- `vpnstar` with `connection_link` reads the provider JSON envelope, selects an
  HTTPS `subscription_url`, `display_link` or `fallback_url`, then fetches the
  subscription without forwarding the API authorization header.
- `vpnstar` also accepts `kind: subscription` when an operator receives a
  direct subscription URL rather than the connection-link API.
- The first URL host is derived from `url`. `follow_hosts` is an exact list of
  hosts the VPNStar envelope may point to; redirects and arbitrary hosts are
  not followed.
- `enabled: false` keeps the provider registered without fetching or serving it.
- `url` and `authorization` are secrets. Never log, commit or paste them into
  tickets. Stored inventory contains neither value nor VLESS user UUIDs.
- `user_agent` and `subscription_user_agent` are optional printable overrides.
- Unknown fields, duplicate provider ids, broad file permissions, private DNS
  destinations and malformed framing fail closed.

The legacy `SUBSCRIPTION_BACKUP_*` manifest is separate and is not accepted by
this ingestion path.

## Two allowlists

Control-plane hosts in the secret manifest only authorize HTTPS fetching.
Data-plane hosts parsed from the VPN subscription must separately be placed in
the provider row's `capabilities.allowed_endpoint_hosts`. An inventory with an
unlisted VPN host remains rejected. Do not copy a provider wildcard or subnet
into either allowlist.

## Admission sequence

1. Confirm written resale/white-label terms and resource limits.
2. Apply migrations; `a-service` and `vpnstar` are seeded disabled.
3. Add both disabled manifest entries and exact control-plane host allowlists.
4. Fill each provider's exact `allowed_endpoint_hosts` from an independently
   reviewed inventory, then enable only ingestion.
5. Fetch and parse with the production User-Agent/device identity. The result
   is a new `quarantined` inventory; it is not served.
6. Run full Xray tunnel, HTTP-status and public-egress checks from BOT and
   independent target networks. A TCP connect or short block page is not a VPN
   verdict. Every configured source must load, finish before the deadline and
   retain at least one live endpoint.
7. Enable liveness and `SPECIAL_MONITOR_PROVIDER_ENABLED`; require a fresh
   `provider` MonitorState matching the current source-set digest.
8. Promote shared A-Service/VPNStar inventory only with the explicit canary
   path. Do not activate it for all users until resale rights and per-user
   credential lifecycle are available.

## Readiness states

- `provider_sources_empty`: no configured source.
- `provider_source_invalid`: URL rejected, commonly by an empty/mismatched host allowlist.
- `provider_liveness_disabled`: selection would ignore measured verdicts.
- `provider_inventory_stale`: no recent out-of-band probe run.
- `provider_inventory_changed`: probe result belongs to an older source set.
- `provider_inventory_unavailable`: the current run found no usable live endpoint.

Raw VLESS URI lists are normalized for the same full-tunnel probe. A served raw
Hysteria URI makes readiness red until a compatible isolated Hysteria probe is
implemented; it is never counted from a TCP-only approximation.

Provider fetches are bounded by `PROVIDER_FETCH_*`. The HTTP client pins a
public DNS result to the TLS connection, verifies SNI/certificate, supports
bounded content-length, connection-close and chunked responses, and never
performs an implicit redirect.

The provider layer and its monitor flag are mandatory for
`validate_scale_readiness` whenever external endpoint delivery is enabled.
Paging remains a separate requirement: do not enable a provider fleet with
nobody accountable for opened/recovered alerts.
