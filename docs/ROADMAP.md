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

1. Rotate 3x-ui admin credentials and panel path atomically across NL and BOT,
   with protected backups and rollback. Do not alter inbounds, client UUIDs,
   Reality parameters or subscription identities.
2. Rotate Redis credentials in a dedicated app/Celery stop → Redis restart →
   app/Celery start window. PostgreSQL must not be restarted.
3. Disable SSH password/root login only after a retained rollback session and
   independent key-only verification.
4. Decide whether to retire stopped legacy application containers/images.
   Shared PostgreSQL and Redis are live dependencies and are excluded from
   legacy cleanup.

## Reliability and operations — P1

- Add an approved external paging destination and accountable on-call owner.
- Provision a second independent origin/ASN before claiming origin redundancy.
- Add scheduled documentation/link validation to CI.
- Continue aggregate drift audits and protected L2 evidence; do not add
  automatic service restarts to monitoring.

## Product and client work — P1/P2

- Publish user-facing subscription import guidance and support flows.
- Measure client/ISP behavior before adding new transports or automatic
  selection claims.
- Evaluate Happ Provider, OpenWrt/PassWall2 and router integrations only when
  their repositories, target devices and owners are available.
- Keep compatibility-only clients untouched until ownership and migration are
  explicitly established.

## Decision principles

No direct-key retirement, broad transport rollout, billing rewrite, inbound
removal or compatibility-client mutation is automatic. Each requires measured
evidence, scoped authorization, rollback and an accountable owner.
