# Safe operations runbook

This runbook describes observation and recovery gates for the live legacy path.
It is not a deployment procedure. Use approved access paths and environment
variables; never paste credentials, UUIDs, bearer subscription URLs, or client
configuration into terminals, tickets, or logs.

## Read-only checks

Run from the approved operations environment only.

1. Confirm aggregate entitlement/control-plane state with the existing
   read-only audit commands. Record only totals, missing/extra counts and pass/
   fail state.
2. Check service status and listeners for relay nginx, NL nginx, x-ui/Xray and
   the bot scheduler. Do not restart a service as part of a check.
3. Verify relay-to-NL reachability and subscription TLS/SNI reachability without
   requesting or printing a bearer URL.
4. Run an approved protected canary E2E only when authorized: import its
   protected configuration transiently, make an HTTPS egress check, and remove
   transient state. A successful TCP connect alone is insufficient.
5. Compare aggregate membership sets only: entitled bot records, 3x-ui DB/API,
   and Xray runtime. `subId` absent from generated Xray client objects is
   expected; membership drift is not.

## Recovery source of truth

1. **Entitlement:** balance relative to tariff in the bot database.
2. **Desired client identity:** entitled `UserVPN` records.
3. **Control plane:** 3x-ui DB/API inventory.
4. **Runtime:** generated Xray configuration and active listener.
5. **Compatibility:** existing runtime clients remain protected until their
   direct links are demonstrably retired.

For a legacy emergency reconciliation, the safety rule is conceptually:

```text
target membership = entitled membership ∪ existing runtime membership
```

Do not replace runtime membership with only the current bot set: that can
remove compatibility clients and strand users with old direct links.

## Mutation and rollback gates

A mutation requires explicit approval, a protected backup of the relevant
control-plane and runtime configuration, before-state aggregates, and a defined
operator/rollback owner. Change only the approved missing membership; do not
rotate client identity or alter transport parameters during recovery.

Proceed only if all post-change gates pass:

- control-plane and runtime services are active and listeners are present;
- entitled-missing count is zero;
- an approved direct legacy E2E and a compatibility E2E pass;
- no new service error condition is observed.

If any gate fails, stop further mutation. Restore the approved protected backup
using the established operations procedure, verify the legacy listener and
protected direct E2E, then investigate before retrying. Do not perform broad
cleanup, client deletion, server reboot, or automatic restart loops.
