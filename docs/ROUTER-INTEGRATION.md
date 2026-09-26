# Router integration contract v1

Status: **frozen v1**. Canonical base URL: `https://special-wifi.link`.

The router consumes a ready-to-run sing-box JSON document. It never downloads,
parses, merges, or assigns trust to A-Service, VPNStar, Lunaire, panel subscriptions, or
any other provider format. Provider ingestion and filtering are server-side
responsibilities.

## 1. Activate a board

`POST https://special-wifi.link/api/v1/vpn/router/activate/`

Request:

```json
{
  "code": "ra1_<opaque-one-time-value>"
}
```

Normative request schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["code"],
  "properties": {
    "code": {
      "type": "string",
      "minLength": 20,
      "maxLength": 44,
      "pattern": "^ra1_[A-Za-z0-9_-]{16,40}$"
    }
  }
}
```

The code is shown in the normal **Подписки → Код для роутера** flow. It expires
10 minutes after issue and can be exchanged exactly once.

Success, HTTP 200:

```json
{
  "device_token": "sp1_<opaque-device-credential>",
  "config_url": "https://special-wifi.link/api/v1/vpn/router/config/"
}
```

Normative success schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["device_token", "config_url"],
  "properties": {
    "device_token": {
      "type": "string",
      "pattern": "^sp1_[A-Za-z0-9_-]{32,96}$"
    },
    "config_url": {
      "const": "https://special-wifi.link/api/v1/vpn/router/config/"
    }
  }
}
```

`device_token` is a secret. Store it in the board's protected persistent
storage, never in logs, crash reports, metrics, URLs, or UI after enrollment.
The server stores only its digest. A lost token requires a new one-time code.

Errors:

- HTTP 400: invalid JSON or request schema; do not retry unchanged input.
- HTTP 404: unknown, expired, or already consumed code; ask for a new code.
- timeout/connection failure: retry the same request once after 2 seconds. If
  the retry returns 404, the first response was ambiguous and a new code is
  required; the API never reveals or recreates a consumed credential.
- HTTP 503: temporary service failure; retry after 5, 15, 30, then 60 seconds,
  bounded by the code's original 10-minute lifetime.

## 2. Fetch configuration

`GET https://special-wifi.link/api/v1/vpn/router/config/`

Headers:

```http
Authorization: Bearer sp1_<opaque-device-credential>
Accept: application/json
Accept-Encoding: gzip
X-Request-ID: <locally-generated-random-id>
```

Success is HTTP 200 with `Content-Type: application/json`, `Cache-Control:
private, no-store`, optional `Content-Encoding: gzip`, and the caller's valid
`X-Request-ID` echoed back. Decompress gzip before JSON validation.

The response is a complete sing-box configuration. Contracted v1 shape:

```json
{
  "log": {
    "level": "warn",
    "timestamp": true
  },
  "outbounds": [
    {
      "type": "urltest",
      "tag": "GLOBAL AUTO",
      "outbounds": ["<endpoint-tag>"],
      "url": "https://cp.cloudflare.com/generate_204",
      "interval": "5m"
    },
    {
      "type": "selector",
      "tag": "COUNTRY <country>",
      "outbounds": ["<endpoint-tag>"]
    },
    {
      "type": "vless | hysteria2",
      "tag": "<endpoint-tag>",
      "server": "<host>",
      "server_port": 443
    }
  ],
  "route": {
    "final": "GLOBAL AUTO",
    "auto_detect_interface": true
  }
}
```

The JSON root is an object with required `log`, `outbounds`, and `route` keys.
`outbounds` is a non-empty array: item 0 is the `urltest` object tagged
`GLOBAL AUTO`; zero or more `selector` objects may follow; remaining objects
are valid sing-box `vless` or `hysteria2` outbounds with unique non-empty tags.
Unknown root keys and unknown fields inside an outbound are accepted for
forward compatibility, but an unknown outbound `type` is rejected. Required
per-type fields are:

| `type` | Required keys | Optional v1 keys |
| --- | --- | --- |
| `urltest` | `type`, `tag`, non-empty `outbounds`, HTTPS `url`, `interval` | none |
| `selector` | `type`, `tag`, non-empty `outbounds` | none |
| `vless` | `type`, `tag`, `server`, integer `server_port`, `uuid` | `flow`, `transport`, `tls` |
| `hysteria2` | `type`, `tag`, `server`, integer `server_port`, `password`, `tls` | none |

`transport`, when present, is a sing-box `grpc`, `httpupgrade`, or `ws`
transport. `tls`, when present, is a sing-box TLS object; Reality requires its
public key. The installed sing-box validator is the normative nested schema and
must accept the whole document before apply.

Every error response uses this schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["detail"],
  "properties": {
    "detail": {"type": "string"}
  }
}
```

There is exactly one `GLOBAL AUTO` outbound and `route.final` selects it. A
country selector is optional and appears only when that country has multiple
valid endpoints. Endpoint-specific sing-box fields are passed through as part
of the complete document; the board must validate them with its installed
sing-box binary instead of inventing a second schema.

Errors:

- HTTP 404: missing, invalid, expired, or revoked device token. Stop polling and
  return to enrollment; these cases are intentionally indistinguishable.
- HTTP 503: no verified provider snapshot is currently publishable. Keep the
  last-known-good configuration and retry with backoff.
- timeout/connection/TLS failure: keep last-known-good and retry with backoff.

Use a normal poll interval of 5 minutes. For HTTP 503 or transport failures use
5, 15, 30, 60 seconds, then 5 and 15 minutes; reset after the next HTTP 200.
Add up to 20% local jitter. Never discard a working configuration because a
poll failed.

## 3. Atomic apply and rollback

For every HTTP 200:

1. Read a bounded response into a new temporary file; decompress if required.
2. Parse JSON and require an object, non-empty `outbounds`, one `GLOBAL AUTO`,
   and `route.final == "GLOBAL AUTO"`.
3. Run the installed sing-box validation command against the temporary file.
4. Preserve the current validated file as LKG, atomically rename the new file
   into place, and reload/restart sing-box.
5. Check that sing-box stays running and that a local routed connectivity probe
   succeeds. On failure, atomically restore LKG and restart once.
6. Promote the new file to LKG only after the health check succeeds.

If there is no LKG on first boot, remain offline and surface an enrollment or
service-unavailable state. Never apply a partially downloaded, unvalidated, or
provider-native document. Local UI may expose country selectors, but unattended
operation always returns to server-defined `GLOBAL AUTO`.

## 4. Correlation and secret handling

Generate a fresh `X-Request-ID` per HTTP attempt and retain only that ID, status,
latency, byte count, and apply result. Never log the activation code,
`device_token`, `Authorization` header, response endpoint credentials, or full
configuration. TLS certificate verification is mandatory; redirects to another
origin are rejected.
