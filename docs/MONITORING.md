# Monitoring

> **Code-ready/local-only, not production deployed.** Local `main` contains the
> implementation from commit `12c8d00`, but local `main` has not been pushed or
> deployed. All schedules and feature flags remain disabled until a reviewed
> rollout.

Monitoring is observational: it must not restart VPN, nginx, Xray, Docker or
relay services. State is sanitized: no UUIDs, bearer subscription URLs, VLESS
URLs, Reality parameters, credentials, or raw client payloads.

## Layers

| Layer | Intended cadence | Purpose |
|---|---:|---|
| L0 | 5 min | Read 3x-ui inbound inventory and check balance-based entitlement against expected inbound properties. |
| L1 | 1 min | TCP/TLS/SNI reachability from the permanent bot-host region; record only endpoint label, regions, port, transport, latency and coarse error class. |
| L2 | 5 min | Protected subscription/direct-key import and HTTPS-egress E2E for the internal canary. |
| L3 | on alert | Manual correlation of nginx, Xray, relay and control-plane state; no automated remediation. |

L0/L1/L2 transition state is intended for sanitized durable storage. L2 must run
on a dedicated `monitoring` queue/container, with concurrency one and isolated
runtime privileges; the ordinary worker must not consume that queue.

## Feature flags and safe configuration

- `SPECIAL_MONITOR_ENABLED=false`, `SPECIAL_MONITOR_L2_ENABLED=false`, and
  `SUBSCRIPTION_DELIVERY_ENABLED=false` are independent default-off gates.
  Monitoring and subscription delivery must be enabled separately by reviewed
  rollout; neither flag authorizes a service restart.
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
and records recovery on the first healthy result after an open alert. External
paging is not included in the current local implementation.

## Production rollout gates

1. Review and deploy the monitoring implementation with every flag false.
2. Apply the required data migration using the approved deployment process; do
   not restart services merely to activate monitoring.
3. Enable L0/L1 and verify two healthy cycles with sanitized status output.
4. Build and start the isolated monitoring worker; verify queue isolation.
5. Enable L2 only for the existing internal canary and verify two runs at least
   five minutes apart.
6. Maintain the canary for 48 hours before a voluntary 3–5 user pilot.

Until every gate is complete, monitoring remains local-only and the subscription
migration may not advance.
