# Roadmap

This file tracks only future work for the **SPECIAL Bot** service. Current
production facts belong in [STATUS.md](STATUS.md). Other VPN projects and their
relay/subscription plans are outside this repository.

## Completed foundation

- Existing customer VLESS/Reality route restored without changing installed
  client identities or transport parameters.
- Balance-based entitlement reconciled against the 3x-ui control plane.
- Custom per-user Django subscription delivery deployed at the domain-backed
  endpoint, with direct `vless://` retained as rollback.
- Billing-to-`expiryTime`/status-label synchronization deployed.
- L0/L1/L2 monitoring deployed with isolated L2 queue and sanitized aggregate
  output.
- One reproducible `vpnbot:latest` image deployed for web, Celery, beat and
  monitoring; Celery workers use `--pool=solo`.
- BOT OOM risk reduced with solo workers and a persistent 1 GiB swapfile.
- BOT subscription origin restricted to the NL nginx host by persistent
  firewall policy.
- The 48-hour soak was explicitly waived by the owner; it was skipped, never
  passed.

## Separately authorized hardening — P0

1. Keep 3x-ui rotation evidence and log suppression healthy; the atomic
   credential/path rotation is complete. Do not alter inbounds, client UUIDs,
   Reality parameters or subscription identities.
2. Keep the completed tracked Redis ownership/credential rotation healthy;
   PostgreSQL remained untouched and old Redis credentials are rejected.
3. Disable SSH password/root login only after a retained rollback session and
   independent key-only verification.
4. Stopped legacy application containers/images are retired. Shared
   PostgreSQL/Redis and compatibility identities remain active and excluded from
   cleanup.

## Reliability and operations — P1

- Enable the implemented provider-neutral paging adapter after an approved
  external destination and accountable on-call owner are supplied.
- Provision a second independent origin/ASN and validate it against the tracked
  origin contract before claiming redundancy.
- Keep repository CI validation green: docs links/stale paths/secret patterns,
  shell syntax, migrations and tests.
- Continue aggregate drift, host-capacity and protected L2 evidence; do not add
  automatic service restarts to monitoring.

## Product and client work — P1/P2

- Maintain the tracked user-facing subscription guide and support-safe evidence
  flow.
- Measure client/ISP behavior before enabling new transports or automatic
  selection claims; use the scale architecture's canary states.
- Implement Happ Provider/OpenWrt/PassWall2 adapters only when their API/repo,
  target devices and owners are available; standard subscriptions remain the
  provider-neutral interface.
- Use the explicit compatibility ownership/migration workflow; keep unowned
  clients untouched.

## Decision principles

No direct-key retirement, broad transport rollout, billing rewrite, inbound
removal or compatibility-client mutation is automatic. Each requires measured
evidence, scoped authorization, rollback and an accountable owner.
