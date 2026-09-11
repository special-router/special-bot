# Provider aggregator architecture

The bot remains the authority for billing, entitlement, device policy and the
public subscription URL. External providers supply only VPN data-plane
capacity. Provider URLs and provider-native profiles are never returned to a
client directly.

## Admission gate

A provider may be enabled for production only when all conditions are true:

- written resale or white-label permission is recorded;
- credentials are isolated per user;
- create, suspend, resume, rotate and revoke are available;
- quota and expiry can be synchronized;
- endpoint inventory passes full-tunnel tests from target networks.
- every data-plane hostname is present in the provider's exact admission
  allowlist.

A shared retail subscription is limited to an internal canary. It cannot
provide selective revocation and must not be treated as production redundancy.
Canary inventory has a separate database state and is never returned by the
production `active_inventory` reader.

## Data model

- `Provider` stores only safe identity, capability and admission metadata.
- `ProviderLease` maps one `UserVPN` to a provider-side lease and an external
  secret reference. Provider credentials are not stored in this table.
- `ProviderInventoryVersion` is an immutable, versioned publication unit.
- `ProviderEndpoint` stores canonical endpoint fields and no user credential.
- `ProviderProbeResult` keys a verdict by endpoint, credential revision and
  vantage.
- `CompiledSnapshot` points to an immutable object outside the relational
  database. The object contains the rendered document; the database stores its
  digest and active revision. Promotion requires the object store to confirm
  that the object exists, is non-empty and has the expected digest.
- `SubscriptionAccessToken` stores only a token digest. The existing `sub_id`
  remains the legacy alias until the config-domain migration is complete.

## Publication flow

```text
provider adapter
  -> validate and normalize
  -> quarantined inventory transaction
  -> required full-tunnel probe verdicts
  -> explicit atomic active inventory promotion
  -> entitlement and client policy compiler
  -> immutable object write
  -> atomic active snapshot switch
  -> config-only HTTP origins
```

The public GET path must read an already published snapshot. It must never
fetch or parse a provider response. Invalid or empty inventory raises before
the previous active version is superseded.

The existing `SUBSCRIPTION_BACKUP_*` mirror path still performs compatibility
fetching in the request path. It is not part of `apps.providers` and must remain
disabled during this migration. It is removed only after the new snapshot
reader is connected to the public endpoint.

## Deployment boundary

`config-api`, Telegram polling, provider ingestion, scheduling and monitoring
use the same application image and Python environment but run as separate
processes. Provider ingestion is routed to the `provider_ingest` Celery queue.
This change intentionally does not add a worker that inherits the full
application environment. A dedicated worker receives a minimal provider-only
environment before production enablement. The A-Service and VPNStar adapters
are registered and their database rows are seeded disabled; neither is served
until secret configuration, host admission, probes and canary promotion pass.

The current renderer and legacy URL remain unchanged. DNS, production flags,
provider credentials and the existing VPN data plane are outside this change.
