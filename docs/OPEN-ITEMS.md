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

### `darkcore-connection-service` on `:3000` — an open client-creation endpoint

**Closed 2026-08-13 with owner approval. Read this before reopening anything.**

A NestJS application, image built locally on 2026-08-05, deployed over Drone SSH,
`network_mode: "host"` — so it bound `0.0.0.0:3000` regardless of its `ports:`
mapping. Its source lives in `/opt/darkcore-connection-service` on NL. `main.js`
is four lines: `NestFactory.create(AppModule)`, `setGlobalPrefix('/api')`,
`listen(3000)`. It registers four routes and **no authentication of any kind** —
grepping the whole built bundle for `UseGuards`, `CanActivate`, `ApiKey` or
`Authorization` returns nothing:

| Route | What it does |
|---|---|
| `GET /api/connections/:id/config` | returns a client's configuration |
| `GET /api/connections/routingconfig` | returns the routing configuration |
| `POST /api/connections` | `createConnection(body.uuid)` — **creates a client in our panel** |
| `POST /api/connections/toggle` | `toggleClient(body.uuid, body.status)` — enables or disables a client |

So anyone on the internet could create a VPN client on our production panel by
posting a UUID, and enable or disable any client in the inbound this service is
scoped to. That scope is a single inbound from its `XUI_VLESS_INBOUND`
environment variable — **13** — which is why inbound 5's customers were never
reachable through it, and why inbound 13 accumulated seven clients that are in
nobody's records. The earlier `500` response was not a rejection; it was the
handler failing on a missing body.

It also carries the panel username and the secret base path in its `.env`, which
is mode `644` — world-readable to any account on the host — and talks to the
panel over plaintext HTTP.

**Done, and reversible:**

- `iptables -A INPUT -i lo -p tcp --dport 3000 -j ACCEPT` followed by
  `-A INPUT -p tcp --dport 3000 -j DROP`. Verified: external `POST` no longer
  connects, `127.0.0.1:3000/api/connections/routingconfig` still answers `200`.
- Inbound 13 disabled through `AsyncInboundApi.set_enabled(13, False)`.
  Reverse with `set_enabled(13, True)`.

The seven clients were cut off by disabling the listener rather than by editing
each client, because this repository has a verified, config-preserving path for
an inbound and none for a per-client edit, and inbound 13 held nothing but those
seven. Port 27914 now refuses connections as a side effect. Before acting, two
independent readings 60 s apart showed inbound 13 moving **0 bytes** with zero
established connections on 27914 or 3000 — nobody was disconnected mid-session.
Panel database backed up to `/etc/x-ui/x-ui.db.bak.20260813-194800` first.

**Still open:**

- **The rule does not survive a reboot.** Nothing on this host makes iptables
  persistent. A restart reopens the endpoint.
- **The panel credential must be rotated.** It sat in a world-readable file
  behind an internet-reachable service; treat it as disclosed.
- **Nobody in this project owns the service.** Whoever deployed it on 2026-08-05
  has to say what it is for. If it stays, it needs authentication and a loopback
  bind of its own rather than a firewall rule compensating for its absence.

### Two clients on inbound 10 that are not ours — still live

Inbound 10 holds two enabled, never-expiring clients, neither in `UserVPN`,
together **9.6 TB**. One is labelled `keenetic1`, i.e. a router; the other alone
accounts for 9.2 TB. They are unrelated to the service above — different inbound,
different mechanism, origin unknown.

**They are carrying traffic right now**: measured twice, 12.5 MB over 90 s and
7.6 MB over 60 s, roughly 1 Mbit/s sustained. Disabling the inbound disconnects
a live session, so it was deliberately left alone.

**And `:80` belongs to them.** `nginx.conf` has a `stream` block —
`listen 80; proxy_pass 127.0.0.1:8080;` — that hands the whole of port 80 to
inbound 10. The port-reduction target of "22/80/443" was therefore preserving
this tenant's front door, not ours. Nothing of ours uses port 80: the panel and
the subscription backend are both reached through `:443`.

**Blocked on:** identifying who they belong to. Then either they are legitimate
and get recorded, or the inbound is disabled and `:80` closes with it.

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

## The test environment is not the production environment

Found 2026-08-13. `ops/scripts/verify_scale_closeout.sh` defaults to
`$ROOT/.venv/bin/python`, and that interpreter diverges from the deployed image
on **ten packages**, not one. The one that was caught first:

| | `.venv` (tests) | image (`requirements.txt`) |
|---|---|---|
| py3xui | 0.7.0 | **0.5.1** |

Those two versions do not agree on how a client is updated — 0.5.1 posts to
`panel/api/inbounds/updateClient/{client_uuid}`, 0.7.0 to
`panel/api/clients/update/{client.email or client_uuid}`. A pin and a guard now
close that specific gap. The others are unguarded: **celery** 5.6.3/5.5.3,
**psycopg** 3.3.4/3.2.10, **redis** 8.1.0/6.4.0, **gunicorn** 26.0.0/23.0.0,
**django-environ** 0.14.0/0.12.0, **requests** 2.34.2/2.32.5, **pydantic**
2.13.4/2.11.9.

The guard was deliberately kept to py3xui alone: a check that fails on ten lines
at once is a check people learn to skip. Widening it is the *second* step; the
first is rebuilding that venv from `requirements.txt` so the ten converge.
psycopg 3.2→3.3 and celery 5.5→5.6 are the two worth reading release notes for.

**Why it stayed invisible:** downgrading to 0.5.1 broke **no test at all**. Every
call site mocks `api.client.update` with an `AsyncMock`, and
`delete_client_by_uuid` builds its own URL on `_post`/`_url`, so the library's
routing is never reached. This repository could have the panel's entire URL
scheme swapped underneath it and stay green. Contract tests asserting the exact
endpoint string are the fix, because a guard compares version strings and only a
contract test compares behaviour.

**Blocked on:** nothing. It is ordinary work, listed here so the next person does
not rediscover it.

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
