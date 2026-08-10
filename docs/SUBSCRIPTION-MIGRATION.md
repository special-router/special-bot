# Subscription migration

> Canonical plan. Current state: subscription delivery is **enabled** and
> deployed. All entitled users have a `subId`; the bot UI issues subscription
> URLs as the primary path with direct `vless://` retained as fallback/rollback.
> The 48-hour soak was waived by owner decision and must never be recorded as
> passed.

## Current state and invariants

The transport is live at `sub.special-wifi.ru`; the URL shape is
`https://sub.special-wifi.ru/sub/<subId>`. `<subId>` is a placeholder only and
must never be placed in documentation, logs, dashboards, or tickets. NL nginx
terminates TLS and proxies `/sub/` from NL to the custom Django subscription
view on BOT. The BOT port is restricted to NL by a persistent `DOCKER-USER`
policy.

This Django endpoint is authoritative for customer delivery. 3x-ui's plain
subscription endpoint cannot be used because it emits the first client UUID of
each inbound instead of the UUID belonging to the requested `subId`; its JSON
variant is not compatible with happ. Django therefore builds the base64 VLESS
payload from the requested `UserVPN` UUID and cached Reality parameters.

A subscription now returns three endpoints:
1. `📊 Подписка-осталось N дней` (or `подписка окончена`) — non-working
   status entry rendered by Django; control-plane record id 1 is preserved but
   runtime-disabled.
2. `🇳🇱 NL Direct` — `sub.special-wifi.ru:8443`, active inbound id 5.
3. `🇳🇱 NL Relay` — `201.34.132.118:443`; the RU relay forwards to active
   inbound 5. Control-plane mirror id 14 is preserved but runtime-disabled.

- Existing direct `vless://` links remain live and are the rollback path.
- Keep the legacy RU relay path unchanged during this migration.
- Balance-based entitlement remains the source of truth. Billing
  enable/disable preserves `UserVPN`, UUID and `subId`. `expiryTime` and the
  status label are mirrored by `sync_expiry_times` from the balance daily.
- Subscription URLs are bearer secrets and may be delivered only privately to
  an entitled user after the relevant gate.
- `subId` preparation is allowed only for an explicit entitled `UserVPN`, one
  record per command. Dry-run and aggregate audit precede every apply batch.
- Client deletion, UUID rotation, inbound/Reality change are not authorized.
  Compatibility-only clients must never be assigned ownership.
- Production has `SUBSCRIPTION_DELIVERY_ENABLED=true`,
  `SUBSCRIPTION_CONNECTOR_ENABLED=true`, `MIRROR_INBOUND_IDS=[14]`,
  `STATUS_INBOUND_ID=1`.

3x-ui remains the source used to create/recover `subId`; Django persists the
result in `UserVPN.sub_id` so the public proxy can resolve a bearer path to the
correct per-user UUID. Its absence from generated Xray client objects is
expected projection, not membership drift.

## Completed

- DNS-only hostname, TLS, nginx SNI routing, and the custom Django `/sub/`
  delivery endpoint are live. Public `:443` URL shape is unchanged.
- Subscription-first bot UI delivered (`get_user_access_url`), deployed, and
  validated by an independent reviewer. Disabled profiles are read-only;
  reactivation reuses identity; direct VLESS stays as fallback.
- `UserVPN.sub_id` is populated for all 65 currently entitled records. The one
  unpaid Django record remains disabled and intentionally has no `sub_id`.
  The 21 compatibility-only inbound clients remain ownership-free and are not
  mutated.
- `MIRROR_INBOUND_IDS=[14]` keeps the preserved Relay metadata synchronized
  with the primary inbound for add/remove/enable and `subId` assignment.
- `STATUS_INBOUND_ID=1` keeps per-client balance-label metadata synchronized;
  the authoritative Django payload renders the dedicated non-working entry.
- Inbounds 1 and 14 are runtime-disabled so Xray has one `:8443` listener. All
  subscription Direct/Relay links preserve the deployed legacy no-flow client
  contract and terminate on inbound 5.
- `sync_expiry_times` runs daily after billing and mirrors `expiryTime`,
  enable state, and the status label to every relevant inbound.
- L2 decodes all subscription entries, selects a non-loopback entry with the
  canary UUID, and verifies both subscription and unchanged direct-VLESS paths.
  L0/L1/L2 are healthy; bounded retry absorbs transient Reality handshakes.
- Celery and monitoring workers use `--pool=solo`, eliminating the prefork
  children responsible for prior OOM pressure. A persistent 1 GiB swapfile is
  active on BOT.
- Gunicorn serves Django with one worker/four threads. NL→BOT `:8001` is allowed;
  direct public access is denied by persistent host and `DOCKER-USER` policy.

## Hardening closeout and remaining gates

- 3x-ui credentials/path were rotated atomically with protected rollback; a
  second rotation invalidated a username exposed by upstream INFO logging after
  the logger was suppressed. Client identities/transports were unchanged.
- Redis ownership now lives in tracked infrastructure Compose; credentials were
  rotated, the old credential is rejected, and PostgreSQL was not restarted.
- Stopped legacy application containers/images were retired. Shared
  PostgreSQL/Redis, data volumes, direct transport rollback and compatibility
  identities remain live.
- SSH password/root hardening still requires a retained rollback session and
  verified key-only access.
- External paging, a second independent origin/ASN, compatibility ownership and
  eventual direct-transport retirement remain separate external/owner gates.