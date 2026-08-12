# Monitoring

> **Deployed and live on the production bot host.** L0, L1, L2 and Host run on
> schedule; L2/Host are confined to an isolated `monitoring` queue and worker.
> `SPECIAL_MONITOR_ENABLED` and `SPECIAL_MONITOR_L2_ENABLED` are false by
> default in `bot/settings.py` and true in the production environment — those
> are two different facts, and [`FLAGS.md`](FLAGS.md) keeps both.

Monitoring is observational: it must not restart VPN, nginx, Xray, Docker or
relay services. State is sanitized: no UUIDs, bearer subscription URLs, VLESS
URLs, Reality parameters, credentials, or raw client payloads.

## Layers

| Layer | Intended cadence | Purpose |
|---|---:|---|
| L0 | 5 min | Read 3x-ui inbound inventory and check balance-based entitlement against expected inbound properties. |
| L1 | 1 min | TCP/TLS/SNI reachability from the permanent bot-host region; record only endpoint label, regions, port, transport, latency and coarse error class. |
| L2 | 5 min | Protected subscription/direct-key import and HTTPS-egress E2E for the internal canary. |
| Host | every 5 minutes | Container-visible `MemAvailable`, swap, load-per-CPU and kernel OOM counter; aggregate values only. |
| L3 | on alert | Manual correlation of nginx, Xray, relay and control-plane state; no automated remediation. |

L0/L1/L2/Host transition state is intended for sanitized durable storage. L2 and Host must run
on a dedicated `monitoring` queue/container, with concurrency one and isolated
runtime privileges; the ordinary worker must not consume that queue.

## Feature flags and safe configuration

Values and production state: [`FLAGS.md`](FLAGS.md#monitoring).

- Monitoring and subscription delivery are independent gates enabled by separate
  reviewed rollouts. Neither flag authorizes a service restart.
- `SPECIAL_MONITOR_ENABLED` and `SPECIAL_MONITOR_L2_ENABLED` are read at import
  time to build `CELERY_BEAT_SCHEDULE`, so changing either takes effect only
  after beat restarts.
- Enable L0/L1 only with a reviewed endpoint matrix and expected inbound
  inventory. Inputs identify endpoints by non-secret labels, not credentials.
- Enable L2 only for the approved internal canary and an explicit expected
  egress value. Missing or invalid required input must fail closed as
  `not_configured`.
- Do not put protected probe configuration, URLs, client identities, or
  authentication material in task results or monitoring state.

## Alert behavior

The intended policy records a first ordinary failure without alerting, opens on
the second consecutive failure, opens immediately for `entitled_missing > 0`,
and records recovery on the first healthy result after an open alert. A
provider-neutral HTTPS webhook adapter is available for opened/recovered
transitions. It remains default-off until an approved destination and on-call
owner exist; payloads contain only layer, transition, coarse error class,
failure count and timestamp.

Production paging enablement is deliberately deferred, not the adapter itself.
The adapter reuses sanitized transition records and remains notification-only:
no automated restart, failover or client mutation.

## Queue isolation

Every service runs the same `vpnbot:latest` image, built and deployed as
described in [`DEPLOY.md`](DEPLOY.md). The isolation that matters here is Celery
routing, not the image: only the `monitoring` worker consumes the `monitoring`
queue, which is what keeps L2 probes off ordinary workers. The Xray binary
present in the other containers is inert — nothing there ever invokes it.

Verify the binary in a freshly built image:

```bash
docker run --rm --entrypoint /usr/local/bin/xray vpnbot:latest version
```

If the build cannot reach the distribution mirror from the default container
bridge, that is a host network condition on the build machine, not a registry
limit. Build with host networking there instead of unpinning base images.

## Production rollout gates

1. Review and deploy the monitoring implementation with every flag false.
2. Apply the required data migration using the approved deployment process; do
   not restart services merely to activate monitoring.
3. Enable L0/L1 and verify two healthy cycles with sanitized status output.
4. Build and start the isolated monitoring worker; verify queue isolation.
5. Enable L2 only for the existing internal canary and verify two runs at least
   five minutes apart.
6. Maintain the canary for 48 hours before a voluntary 3–5 user pilot.

Gates 1–5 are **complete**: the implementation is deployed, the migration was
applied, L0/L1 report healthy cycles, the isolated monitoring worker runs with
queue isolation verified, and two L2 runs passed five minutes apart.

The 48-hour soak in gate 6 is **waived by owner decision for delivery speed**.
It is recorded as skipped, never as passed: the observation evidence it would
have produced does not exist, so any regression it would have caught is an
accepted risk carried into the pilot.

## Operational notes

- HTTP client logging is pinned to `WARNING` for `httpx`/`httpcore`. Those
  loggers emit full request URLs at `INFO`, which exposes the secret 3x-ui panel
  base path. Do not lower that level.
- Control-plane reads require two consecutive identical client-ID snapshots. A
  single incomplete panel response therefore surfaces as `control_plane`, not as
  a false `entitled_missing` alert.
