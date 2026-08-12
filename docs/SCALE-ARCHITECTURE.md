# Scale-ready architecture

> **This document describes a target, not the deployment.** Nothing here is a
> claim about what runs today; for that see [STATUS.md](STATUS.md) and
> [ARCHITECTURE.md](ARCHITECTURE.md). The thresholds below are engineering
> triggers to re-measure against, not measured capacity.

## Target state

The target is a subscription-first SPECIAL Bot platform that scales application
and transport capacity independently. Legacy application containers and
installed direct-key-first flows are migration inputs, not permanent control
plane components.

```text
Telegram bot / API
  → Django application replicas (stateless except DB)
  → PostgreSQL primary source of entitlement and subscription identity
  → Redis broker/cache with bounded queues
  → subscription renderers behind TLS origin(s)
  → regional Xray origins managed through explicit control-plane adapters
  → client-side endpoint selection from one per-user subscription
```

### Ownership boundaries

- PostgreSQL owns users, balance entitlement, `UserVPN` UUID and persisted
  `sub_id`.
- Django owns customer delivery and renders per-user payloads. 3x-ui plain
  subscription output is never customer-authoritative.
- Origin adapters own transport membership; they must be idempotent and preserve
  the existing UUID/subscription identity during migration.
- Redis is coordination infrastructure, not a source of entitlement truth.
- Monitoring observes and pages; it never restarts, fails over or mutates
  customers automatically.

## Capacity model and thresholds

These are engineering triggers, not claims of current capacity. Re-measure under
representative traffic before each step.

| Component | Scale signal | Action threshold |
|---|---|---|
| BOT memory | `MemAvailable`, swap, OOM counter | alert below 128 MiB, missing 512 MiB swap, or any new OOM; resize before sustained <20% free |
| Django subscription renderer | p95 latency/error rate, requests/sec | add a separate web replica when p95 exceeds 500 ms or CPU remains >70% for 15 min |
| Telegram worker | update backlog/processing latency | split bot polling/webhook process from HTTP renderer before adding web replicas |
| Celery ordinary queue | oldest task age and queue depth | add solo workers when oldest task >60 s; keep L2 isolated |
| PostgreSQL | connections, CPU, slow queries, storage | add pooling/read tuning before 60% connection budget or p95 query >100 ms |
| Redis | memory, evictions, command latency | alert on any eviction or sustained p95 >10 ms; configure limits before growth |
| Subscription origin | TLS error rate and p95 response | add independently routed renderer/origin before p95 >500 ms or single-origin SLO risk |
| Xray origin | active clients, CPU, memory, handshake failures | add a distinct origin before sustained CPU >70%, memory >75%, or regional error budget burn |
| Control plane | reconciliation duration/rate limits | batch and shard origin reconciliation before a full pass exceeds five minutes |

Before onboarding materially more users, move Django HTTP rendering and Telegram
bot execution into separate services, use DB connection pooling, add resource
limits/reservations, and load-test subscription rendering without real bearer
values.

## Endpoint and transport portfolio

Represent endpoints as non-secret descriptors: stable ID, public address,
region, provider/ASN, transport type, priority, canary state and enabled state.
Do not store client UUIDs or Reality secrets in endpoint inventory. A transport
moves through `disabled → internal canary → bounded pilot → production` only
after client × ISP evidence, protected L2 checks and rollback validation.

Client-side selection is preferred because it survives origin loss without a
central traffic proxy. Server-side DNS/origin failover remains a separate,
operator-approved layer. A relay in front of the same origin is not independent
redundancy.

## Provider and router interfaces

- Customer subscription output remains standards-based base64 VLESS.
- Provider-specific formats are adapters over a sanitized endpoint portfolio,
  not new entitlement stores.
- Happ Provider integration requires its documented API/format and an owner;
  until then happ consumes the standard subscription URL.
- OpenWrt/PassWall2/router support requires a maintained repository, target
  hardware matrix and canary devices. Bot-side output must stay portable and
  must not depend on one router vendor.

## Compatibility ownership and migration

The operational procedure is [COMPATIBILITY-MIGRATION.md](COMPATIBILITY-MIGRATION.md);
it wins on any detail. The phases below place that procedure in the scale plan.

Compatibility-only clients have no inferred owner. Migration requires an
explicit mapping record created from private owner verification. Never map by
remark, traffic pattern, Telegram guess or IP address.

Phases:

1. **Observe:** keep compatibility clients enabled; collect only aggregate
   counts and last-known migration state.
2. **Claim:** support verifies ownership privately and records a one-to-one
   mapping without changing UUID.
3. **Deliver:** issue the owner's subscription URL and retain the existing
   direct key as rollback.
4. **Confirm:** obtain repeated protected evidence that the subscription path is
   active; do not infer use from control-plane traffic alone.
5. **Retire direct-first support:** only after every entitled owner is migrated,
   rollback period elapsed and owner approval is recorded.
6. **Remove legacy application assets:** stopped app containers/images only;
   PostgreSQL/Redis and active compatibility clients are excluded.
7. **Remove legacy transport:** separate approval after zero unmapped clients,
   zero direct-only support cases and rollback evidence.

## Gates for a no-legacy production claim

- zero unowned compatibility clients;
- zero customer flows requiring the old app containers;
- subscription renderer and Telegram process independently scalable;
- at least two healthy origins with different provider/ASN;
- capacity/load evidence at projected user count plus 2× burst;
- current backup/restore drill for PostgreSQL, Redis configuration and origin
  membership;
- externally delivered alerts with an accountable owner;
- direct key retirement explicitly approved after migration evidence.
