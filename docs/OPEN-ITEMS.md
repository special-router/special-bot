# Open items and what blocks them

Everything unfinished, with the specific thing standing in the way. If an item
needs a human — a Telegram account, a purchase, an owner's decision — it says
so, because that is the difference between work an agent can pick up and work it
cannot.

Reviewed **2026-08-12**. Host-side facts here are operator-reported and are not
checkable from the repository.

---

## Shipped but inert

Code is merged, tested and deployed; the feature does nothing until someone
outside this repository acts.

### Support tickets

In-bot tickets with a forum-topic thread per user. `register_handlers.py`
registers nothing while `SUPPORT_CHAT_ID` is `0`, so the menu button stays a
plain link to the public chat and the bot does not start reading private
messages for a disabled feature.

**Blocked on, in this order:** create a supergroup and turn Topics on in its
settings; add the bot as an administrator with *Manage topics* allowed; then set
`SUPPORT_CHAT_ID`. Setting the id without both steps leaves every first message
failing on `create_forum_topic` — the user is told support is unavailable, no
ticket is left behind, and nothing gets through.

### Premium button icons

`TELEGRAM_BUTTON_ICONS_ENABLED=false`. Bot API accepts `icon_custom_emoji_id`
only for a bot whose owner holds Telegram Premium, and the code cannot see that
status. Enabled without Premium, the whole keyboard is rejected, not just the
icon.

**Blocked on:** the bot owner holding Telegram Premium, then a check on the live
bot. Every icon already has a mandatory plain-emoji fallback, which is what
renders today.

### External paging

`SPECIAL_MONITOR_PAGING_ENABLED=false`. The provider-neutral HTTPS webhook
adapter is deployed and sends only layer, transition, coarse error class,
failure count and timestamp.

**Blocked on:** an approved external destination and a named on-call owner. Do
not describe paging as live before both exist.

### External backup endpoints, beyond the first user

**Live since 2026-08-13, for `UserVPN` 801 alone.**
`SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=true` with
`SUBSCRIPTION_BACKUP_TEST_USER_IDS=[801]`. One provider is configured; its 9
regions render as 8 country lines, each verified by a real Reality handshake with
a distinct egress IP. 57 of 58 active subscriptions are untouched at three lines.

**Blocked on an owner decision, and two facts that decision has to answer:**

- **One shared upstream UUID for every customer.** Nobody can be revoked
  individually, per-user consumption is invisible, and one abuser costs all of
  them the account at once.
- **The provider's device limit is 1/1.** The account is a single seat, so our
  refreshes and the operator's own client compete for it. Raising it happens in
  the provider's cabinet, not here.

Per-user upstream credentials or chaining the provider through our own egress
each remove both; neither is built. See
[`MIRROR-INBOUNDS-RUNBOOK.md`](MIRROR-INBOUNDS-RUNBOOK.md#7-rolling-out).

### Internal same-origin canary — its memberships are gone

`SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=false` and
`SUBSCRIPTION_INTERNAL_MEMBERSHIP_SYNC_ENABLED=false`, restricted to `UserVPN`
801 across inbounds 7/9/13 and 10.

**Checked 2026-08-13: 801's UUID is present in inbounds 1, 5 and 14 only.** It is
in none of 7, 9, 10 or 13. The provisioning that the canary depends on no longer
exists, so enabling the flag today renders nothing — the code fails closed, which
is why this is a stale premise rather than an incident. **Re-provision and
re-verify the memberships before treating any part of the canary section of
[`MIRROR-INBOUNDS-SPEC.md`](MIRROR-INBOUNDS-SPEC.md) as current.**

**Also blocked on:** an owner decision. The earlier blocker — a plaintext-HTTP
panel control plane — is resolved for *our* client: the panel is HTTPS-only and
`utils/py3xui/async_api.py` refuses anything else. The service on `:3000` still
uses plaintext. Enable membership sync before rendering, never the other way round.

---

## Device binding phase 2

Phase 1 is live: `SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED=false` in the
running `.environment`, so devices bind unattended while real fleets register
their real devices. **The value in `bot/settings.py` is `True`** — phase 1 exists
only because the environment overrides it, and a fresh deployment that forgets
the override starts in phase 2 by accident.

Phase 2 is flipping it to `true`, which closes the path where a leaked `sub_id`
spends both device slots and locks the customer out. The plan was roughly 48
hours of unattended binding from the rollout, putting the flip around
**2026-08-14**.

**Blocked on:** a human deciding the fleet has bound enough devices, and editing
`.environment` on BOT. Nothing in the code schedules it.

---

## Reducing the NL host to 22/80/443

Externally reachable on NL as last surveyed: **22, 80, 443, 2096, 3000, 8080,
8443, 27914**. The intent is 22/80/443 only. Each remaining port is blocked for
its own reason, and none of them is "nobody got around to it":

| Port | Why it is still open |
|---|---|
| `2096` | Live external clients are connected. Closing it disconnects paying users. Verify current connections before touching it. |
| `3000` | **Identified 2026-08-13** — see below. A NestJS control plane over our own 3x-ui panel, owned by someone else. |
| `8080` | Inbound 10. **Not diagnostic-only:** two clients, neither in our database, **9.6 TB** consumed. |
| `8443` | The primary VLESS/Reality inbound. Not closable — it is the product. |
| `27914` | Inbound 13. Seven clients, **none in our database**, 1.16 TB. Toggled by the service on `:3000`. |

**Measured 2026-08-13, and only one of these is firewalled.** `iptables -S INPUT`
on NL carries exactly two rules, both for the panel port `23133`. Ports `2096`,
`3000`, `8080` and `27914` have no rule at all. The host has been up 9 days, so
nothing was lost to a reboot — the rules were never added.

### `darkcore-connection-service` on `:3000`

A NestJS application, image built locally on 2026-08-05, deployed over Drone SSH,
published on `0.0.0.0:3000`. It logs into our 3x-ui panel and drives inbound 13
(`POST /api/connections`, `POST /api/connections/toggle`). Four separate problems,
in order of how much they cost if ignored:

1. **Reachable from the internet and unauthenticated.** `POST /api/connections`
   from an unrelated host answers `500`, not `401` — an outside caller is already
   executing application code that holds panel credentials.
2. **It carries the panel username and the secret base path in plain container
   environment.** That path is bearer access to the whole control plane; this
   repository treats it as a secret and pins three loggers to `WARNING` to keep
   it out of logs. It sits in `docker inspect` output.
3. **It talks to the panel over plaintext HTTP**, which our own client refuses at
   construction time. Loopback-only, so not on the wire — but it proves the panel
   still accepts plaintext locally.
4. **Nobody in this project owns it.**

**Blocked on:** an owner decision about whether this service stays. If it stays,
it needs authentication and a bind to `127.0.0.1`, and the panel credential it
holds must be rotated because it has been reachable. If it goes, stopping it
disables whatever drives inbound 13's seven clients — establish who they are
first.

### Nine clients that are not ours

Inbounds 10 and 13 hold nine enabled, never-expiring clients. **Not one of their
UUIDs is in `UserVPN`**, and together they have moved **10.8 TB** — a sixth of
what our whole product inbound has moved. One is labelled `keenetic1`, i.e. a
router, and one alone accounts for 9.2 TB. They pay us nothing through this
system and are invisible to billing.

**Blocked on:** identifying who they belong to before disabling anything —
the same rule that found the `2096` users.

### No per-client traffic attribution on the product inbound

Every client entry in inbound 5 has an **empty email**, deliberately: py3xui's
generic `delete()` resolves a client through its email first, and an empty email
made it delete the wrong client. `delete_client_by_uuid` fixed the deletion
hazard, but the empty labels stayed and xray keys its statistics by email.

The consequence is measurable: inbound 5 reports **58 TB** moved, and
`client_traffics` accounts for **13.7 TB**, all of it historical rows for labels
that no longer exist on any client. Today, nothing accumulates. **We cannot tell
who consumes what, cannot detect a shared key, and cannot enforce a quota.**

**Blocked on:** a decision about labels. Restoring a per-client identifier
restores attribution and no longer reintroduces the deletion hazard, but the
identifier must not be anything that identifies a customer to whoever reads the
panel.

Related and worth knowing before any firewall work: **inbound 13 shares its
Reality SNI with inbound 5**, so a rule aimed at one can silently affect the
other.

**The iptables rules currently in place are not persistent across a reboot.**
A reboot re-opens what was blocked. Treat every "port is closed" claim as valid
only until the next restart.

**Rule for this whole area:** check for established connections and ownership
*before* blocking a port or disabling an inbound. This is not caution for its own
sake — it is how the `2096` clients were found.

---

## Not built

### Per-UUID inbound diagnostics

[`INBOUND-DIAGNOSTICS-SPEC.md`](INBOUND-DIAGNOSTICS-SPEC.md) is a design. None of
the synthetic per-client probing exists. Current monitoring proves the *path* is
alive through one canary UUID; a client whose own `enable`/`expiryTime`/flow is
wrong is invisible to it. The 2026-08-11 incident — two clients expired in 3x-ui
while the path canary stayed green — is exactly this gap, and it was found by
hand.

The symptom→layer tables in that document are useful today even though the
automation is not.

### Second independent origin

Bounded scale-readiness reports redundancy as **not ready**, deliberately. A
relay in front of the same origin is not redundancy. Requires a second
provider/ASN validated against the tracked origin contract.

---

## Accepted risks and cleanup

- **The 48-hour monitored canary soak was waived by owner decision** for
  schedule reasons. It is recorded as skipped, never as passed. The observation
  evidence it would have produced does not exist.
- **21 compatibility-only 3x-ui clients have no verified owner.** They stay
  enabled and are never mutated, and ownership is never inferred from remark,
  traffic, IP or a Telegram guess. See
  [`COMPATIBILITY-MIGRATION.md`](COMPATIBILITY-MIGRATION.md).
- **`SUBSCRIPTION_RELAY_HOST` and `SUBSCRIPTION_RELAY_PORT` are dead settings.**
  Nothing outside `bot/settings.py` reads them; the Relay endpoint comes from
  `Server.client_vpn_host`. They are kept only because removing a setting that
  might be present in a live `.environment` is a deploy-time decision, not a
  code cleanup.
- **RU relay administration still uses a password through `sshpass`**, which the
  rest of this repository forbids. It is a named exception, not an oversight —
  see [`RUNBOOK.md`](RUNBOOK.md#ru-relay-administrative-access). Migrating it to
  a key needs a retained provider-console rollback session.
