# Roadmap

Statuses below are rebased to the current snapshot, not historical plans.

## Completed foundation

- **Legacy stabilization:** completed for the current route; legacy delivery is
  live with balance-based entitlement and compatibility preservation.
- **Domain subscription canary:** completed for transport and one internal
  protected canary; this is not customer rollout.

## Pending gates — P0

1. Obtain approved key-based access to the bot production host. This is the
   current hard blocker: monitoring cannot be deployed without it.
2. Deploy the reviewed monitoring implementation and enable sanitized L0/L1/L2
   by gate. The 48-hour soak is waived by owner decision for schedule reasons;
   the pilot then relies on repeated protected L2 evidence instead.
3. Run an explicitly approved voluntary pilot of 3–5 users, preserving direct
   VLESS rollback.
4. Enable opt-in bot subscription delivery only after pilot evidence.
5. Separately design/test billing and `expiryTime` synchronization; preserve
   balance-based entitlement until then.

## Transport and resilience — P1/P2

- Add a second independent origin/ASN before relying on a single NL origin.
  Deferred: requires a provisioned second origin and an approved budget/owner
  decision, so it cannot be implemented from the current environment.
- External paging for monitoring alerts is deferred: it needs an approved
  notification channel and on-call owner. Until then, alert state is only
  readable through sanitized monitoring records and the audit command.
- Test multitransport incrementally: keep Reality/TCP primary; evaluate XHTTP
  and Hysteria2 only on separate test paths with ISP × client evidence.
- Build a measured subscription/transport portfolio and regional health model;
  do not promise bypass behavior without representative field results.
- Evaluate own aggregation and external-provider integrations only as separate,
  reviewed work; do not mix them into the legacy migration.

## Product and router work

- Voluntary bot delivery and user-facing installation guidance after migration
  gates.
- OpenWrt/PassWall2 router proof of concept: subscription import, transparent
  routing and health-based fallback, kept separate from billing control plane.
- Router hardware/software integration after the source repository, target
  hardware constraints and support model are available.
- Later: retention, analytics, multi-region selection, and evidence-driven
  panel/control-plane evaluation after data-model stabilization.

## Decision principles

No panel migration, direct-link retirement, broad transport rollout, or billing
rewrite is automatic. Each requires measured evidence, rollback, scoped canary
and explicit approval.
