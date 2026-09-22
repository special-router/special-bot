# `special-wifi.ru` retirement

Status: **stage 1 in progress**. The old domain remains compatibility-only and
must not be removed while it still carries successful requests.

## Target topology

| Purpose | Canonical endpoint | Rule |
| --- | --- | --- |
| Router activation/config | `https://special-wifi.link` | The only router API base. |
| Remnawave API/UI | `https://panel.special-wifi.link:8843` | Control plane only; preserve the existing private base path. |
| Owned VPN/XHTTP | `vpn.special-wifi.link` | Data plane only; never fall back to the panel hostname. |
| Subscription delivery | existing canonical config edge | Independent from panel and VPN hosts. |

`Server.vpn_url` remains the panel control-plane URL. Locally rendered direct
fallbacks use the explicit `SUBSCRIPTION_DIRECT_VPN_HOST` setting. Code must not
derive a client endpoint from `Server.vpn_url`, even during panel failure.

## Stage 1 procedure

1. Publish DNS-only A records for `panel.special-wifi.link` and
   `vpn.special-wifi.link` to the NL origin. Do not proxy either record through
   a CDN that cannot pass the owned protocols.
2. Extend the NL TLS certificate and HTTP/SNI routing for both names. The panel
   hostname serves only the existing Remnawave API/UI upstream on port 8843.
   The VPN hostname serves owned VPN/XHTTP paths; it is never a panel alias.
3. Smoke-test the new panel hostname over TLS and make an authenticated,
   read-only Remnawave API request. Only after that succeeds, replace the host
   in `REMNAWAVE_API_URL` and `REMNAWAVE_SUBSCRIPTION_BASE_URL`, preserving
   their current port and private path.
4. Set `SUBSCRIPTION_DIRECT_VPN_HOST=vpn.special-wifi.link`. Add the new names
   to `ALLOWED_HOSTS`/CSRF origins where applicable and to the L1 monitoring
   matrix. Deploy and render all active subscription documents.
5. Assert that no active rendered document contains `special-wifi.ru`. This is
   a content check over decoded payloads; HTTP health alone is insufficient.
6. Leave the old `/sub/` and `/assets/v1/` listeners intact. Count successful
   responses per UTC day without logging bearer paths or client identifiers.

Stage 1 is reversible: restore the previous environment backup and nginx
configuration, reload nginx, and redeploy the previous application image. It
does not recreate or restart PostgreSQL or Redis.

## Final retirement gate

Removal is forbidden until all of the following are true:

- successful old-domain `/sub/` responses are 0 for seven consecutive complete
  UTC days;
- successful old-domain `/assets/v1/` responses are 0 for the same seven days;
- the Remnawave API is fully served through `panel.special-wifi.link`;
- the old-domain occurrence count across every active rendered subscription is
  0;
- or an explicit owner override records the accepted traffic risk and date.

The window resets to day 0 after any successful old-domain request. The current
task cannot honestly complete a seven-day observation window; its closeout must
state the most recent per-day counts and the exact remaining gate.

Baseline captured from aggregate nginx logs on 2026-09-22 (UTC; successful is
HTTP 2xx/3xx):

| UTC day | `/sub/` successful | `/assets/v1/` successful |
| --- | ---: | ---: |
| 2026-09-15 | 8 | 2107 |
| 2026-09-16 | 5 | 644 |
| 2026-09-17 | 0 | 0 |
| 2026-09-18 | 0 | 0 |
| 2026-09-19 | 4 | 1343 |
| 2026-09-20 | 6 | 2069 |
| 2026-09-21 | 9 | 2174 |
| 2026-09-22, partial | 9 | 1383 |

Therefore the current consecutive-complete-day zero window is **0/7**. The
baseline contains counts only; request paths, subscription identifiers, client
addresses, and credentials were not retained in this document.

Only after the gate passes may one change remove the old DNS records, nginx/SNI
listeners, certificate SAN, `ALLOWED_HOSTS`/CSRF compatibility values,
monitoring entry, and documentation references. Active XHTTP traffic must never
be cut merely because the control-plane migration succeeded.
