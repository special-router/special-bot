# SPECIAL production monitoring

Monitoring is part of the tracked `special-bot` deployment. It does not use a
local workstation scheduler and never restarts VPN, nginx, Xray, Docker or relay
services.

## Layers

- **L0 / 5 minutes:** reads 3x-ui inbound inventory and verifies the deployed
  balance-based entitlement invariant.
- **L1 / 1 minute:** runs TCP reachability probes from the permanent bot host.
  Every endpoint record includes probe region and target region.
- **L2 / 5 minutes:** optional protected subscription/direct-key VLESS E2E on a
  dedicated `monitoring` Celery queue and container containing Xray.

State and alert transitions are stored in PostgreSQL:

- `MonitorState`: current state per layer;
- `MonitorTransition`: sanitized `opened`/`recovered` history.

The Django admin is read-only for both models. Task results and status commands
contain no UUIDs, bearer URLs, VLESS URLs, Reality parameters or credentials.

## Activation

All monitoring schedules default to disabled.

L0/L1 on the production bot host require:

```dotenv
SPECIAL_MONITOR_ENABLED=true
SPECIAL_MONITOR_PROBE_REGION=ru-bot
SPECIAL_MONITOR_FAILURE_THRESHOLD=2
SPECIAL_MONITOR_ENDPOINTS=<compact JSON from ops/monitoring/inbound-matrix.json, with endpoint renamed to name>
SPECIAL_MONITOR_EXPECTED_INBOUNDS=[{"server_id":1,"inbound_id":5,"port":8443,"protocol":"vless","network":"tcp","security":"reality"},{"server_id":1,"inbound_id":10,"port":8080,"protocol":"vless","network":"grpc","security":"reality"}]
```

The approved endpoint matrix is tracked at `ops/monitoring/inbound-matrix.json`.
Its `endpoint` field maps to the runtime `name` field. The host is deployment
input and is not logged in `MonitorState`; only endpoint name, target region,
port, transport, result, latency and coarse error class are persisted.

`SPECIAL_MONITOR_EXPECTED_INBOUNDS` is optional but recommended. When supplied,
L0 immediately alerts on port/protocol/network/security drift for the listed
server/inbound IDs. Client-count drift remains visible in PostgreSQL for
operator correlation; entitled-client loss alerts immediately.

L2 additionally requires:

```dotenv
SPECIAL_MONITOR_L2_ENABLED=true
SPECIAL_MONITOR_CANARY_USER_VPN_ID=<internal-record-id>
SPECIAL_MONITOR_EXPECTED_EGRESS=<expected-public-ip>
SPECIAL_MONITOR_HEALTH_URL=https://api.ipify.org
SPECIAL_MONITOR_XRAY_PATH=/usr/local/bin/xray
```

`SPECIAL_MONITOR_EXPECTED_EGRESS` must be an explicit IPv4/IPv6 address. The
health URL must be HTTPS and cannot contain credentials. Invalid or missing
values fail closed with `not_configured`; L2 never infers an egress address from
a `Server` record.

Start the isolated monitoring worker explicitly:

```bash
docker compose --profile monitoring up -d monitoring
```

The ordinary `celery` worker consumes only the default `celery` queue. L2 is
routed to the dedicated `monitoring` queue, concurrency 1, read-only rootfs,
`no-new-privileges`, no Linux capabilities and tmpfs `/tmp`.

## Rollout

1. Apply database migration.
2. Deploy app code with both flags false.
3. Enable L0/L1 and verify two healthy cycles:
   ```bash
   docker exec vpn_bot-django_web-1 python manage.py audit_special_monitoring --json
   ```
4. Build and start the `monitoring` profile. The linux/amd64 image is
   reproducible from the repository and copies Xray 26.6.1 from its pinned
   manifest digest.
5. Enable L2 only for the existing internal canary.
6. Verify two L2 cycles at least five minutes apart.
7. Keep the internal canary for 48 hours before a voluntary pilot.

## Alert semantics

- First ordinary failure: record state, no alert.
- Second consecutive failure: open alert.
- `entitled_missing > 0`: open alert immediately.
- First healthy result after an alert: record recovery.

No external notification sender is included yet. PostgreSQL/admin status is the
first durable phase; Telegram/email paging is a separate reviewed change.
