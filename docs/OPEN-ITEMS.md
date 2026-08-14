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

Was **22, 80, 443, 2096, 3000, 8080, 8443, 27914**. Now **22, 80, 443, 8443,
2096**, verified from a second host on 2026-08-13. Three ports closed that day,
none of them costing a live connection:

| Port | State | Why |
|---|---|---|
| `2096` | **open** | 12 established external connections. Closing it disconnects paying users. Verify current connections before touching it. |
| `8443` | **open** | The primary VLESS/Reality inbound. Not closable — it is the product. |
| `3000` | closed | An unauthenticated client-creation endpoint; see below. |
| `8080` | closed | Inbound 10's own port. **Every connection to it came from `127.0.0.1`** — nginx proxying from `:80`, no direct external user at all — so blocking it externally cost nothing and the inbound kept running. Measured after the rule: 16 MB in 60 s, still flowing. |
| `27914` | closed | Inbound 13, itself disabled. Blocked so re-enabling the inbound cannot silently re-expose the port. |

Each closed port has a pair of rules — `-i lo … -j ACCEPT` then `-j DROP` — so
loopback callers keep working and only the outside is refused.

**The rules now survive a reboot.** `/etc/systemd/system/iptables-restore.service`
is a `Type=oneshot` unit ordered `Before=network-pre.target`, restoring
`/etc/iptables/rules.v4` (mode 0600), enabled and **exercised** — started once
and the ruleset re-read identical, so it is verified rather than merely
installed. No package was installed; `iptables-save`/`iptables-restore` were
already present. To change the rules, edit them live and re-run `iptables-save`
into that file, or the next reboot silently reverts your change.

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

**Decided, and off until someone turns it on.**
`CLIENT_TRAFFIC_LABELS_ENABLED` defaults to `false`, so deploying this changes
nothing: with it off every client write leaves `email` exactly as it found it,
and `backfill_client_labels --apply` refuses. With it on, every client we write
carries `uv-<inbound_id>-<UserVPN.id>`, stamped inside the panel transport
(`apps/servers/client_labels.py`) so no call site can forget it. `UserVPN.id` is a surrogate key that identifies nobody to
whoever reads the panel; the inbound prefix keeps it unique under the panel-wide
UNIQUE on `client_traffics.email` if the mirror inbounds ever wake. A label the
transport did not write — the status inbound's `осталось N дней`, or a hand-made
one — is never overwritten.

**A label is written only on an inbound we own**, which is the primary inbound
configured on the owner's `Server` row and nothing else — read from the database,
not from a constant. Inbounds 10 and 13 belong to the foreign tenant above, and
a write there is one we had no business making; the status, mirror and canary
inbounds are a second record of a customer we already attribute. Anything the
transport cannot positively establish as ours stays unlabelled, which costs
attribution on that client and nothing else. Existing clients are repaired by
`manage.py backfill_client_labels --server-id N --inbound-id M [--apply]`, which
is a dry run unless `--apply` is passed and refuses to write a colliding label.

**The consequence, and the whole reason for the flag: with labels on, x-ui
becomes a second actor able to disable a customer.**
x-ui enforces `total` and `expiryTime` per client off `client_traffics`, and
that enforcement is inert today only because no rows exist. Labelling creates
the rows and switches it on — which is why it is a deliberate `.environment`
edit rather than something acquired by shipping unrelated work. Setting
`CLIENT_TRAFFIC_LABELS_ENABLED=false` again stops new labels, but does not
remove the rows already created; those are cleared in the panel. On day one it agrees with the bot: `total = 0` on
every client on inbound 5, so no quota can trip, and `sync_expiry_times` pushes
a fresh `expiryTime` daily. It stops agreeing the moment that daily push does
not run — if `sync_expiry_times` or `update_user_vpn` stalls past a day, x-ui
disables paid-up customers on its own, without the bot knowing and without any
of our logs recording it. A stalled scheduler used to mean "state drifts"; it
now means "customers get cut off". Anyone who sets a non-zero `total` on a
client is arming a second, independent kill switch.

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

Found 2026-08-13, **closed 2026-08-13**. `ops/scripts/verify_scale_closeout.sh`
defaults to `$ROOT/.venv/bin/python`, and that interpreter diverged from the
deployed image on **ten packages**, not one. The one that was caught first:

| | `.venv` (tests) | image (`requirements.txt`) |
|---|---|---|
| py3xui | 0.7.0 | **0.5.1** |

Those two versions do not agree on how a client is updated — 0.5.1 posts to
`panel/api/inbounds/updateClient/{client_uuid}`, 0.7.0 to
`panel/api/clients/update/{client.email or client_uuid}`. The other nine were
celery, psycopg, redis, gunicorn, django-environ, requests and pydantic among
them.

Both steps are done. The venv was rebuilt from `requirements.txt` on Python
3.13.13, matching the image, and `ops/scripts/validate_repository.py` now checks
**all 44 pins**, not py3xui alone — a drifted package is named with both
versions and the message says to rebuild, never to loosen a pin. The summary
line carries `pins=44/44`.

Two rules make that check usable in both places it runs:

- **Absence is not drift.** A package the interpreter does not have says
  nothing; only a version that contradicts a pin fails.
- **The exemption belongs to the invocation, never to the interpreter.** A bare
  `validate_repository.py` is a repository lint and passes anywhere
  (`pins=not-requested`); `--check-pins` and `conftest.py` enforce, so every
  interpreter that collects the suite is checked. An earlier version keyed the
  exemption off `sys.prefix != sys.base_prefix`, which disabled the guard inside
  the image — `Dockerfile` installs `requirements.txt` into the base interpreter
  of `python:3.13-slim` and creates no venv — and let
  `SPECIAL_VERIFY_PYTHON=/usr/bin/python3` produce a green suite run. This
  machine's `python3` has 34 of the 44 installed, 21 of them drifted.

**The pin nobody wrote is written now.** All eleven dependencies in
`pyproject.toml` carry the version `requirements.txt` had already compiled, so
the resolver has no freedom left to drift into: a venv built from either file
lands on the same tree. Recompiling with the pins in place changes no version —
the only diff is cosmetic (`Pillow`→`pillow`, sort order, and the Windows-only
`colorama`, which a Linux compile drops because the committed file carries it
without its marker).

Upgrading is now an explicit edit to `pyproject.toml` followed by
`uv pip compile pyproject.toml -o requirements.txt`, which is the point rather
than the cost. psycopg 3.2→3.3 and celery 5.5→5.6 are the two worth reading
release notes for before any deliberate bump.

**Why it stayed invisible:** downgrading to 0.5.1 broke **no test at all**. Every
call site mocks `api.client.update` with an `AsyncMock`, and
`delete_client_by_uuid` builds its own URL on `_post`/`_url`, so the library's
routing is never reached. This repository could have the panel's entire URL
scheme swapped underneath it and stay green. Contract tests asserting the exact
endpoint string are the fix, because a guard compares version strings and only a
contract test compares behaviour.

**Blocked on:** nothing. It is ordinary work, listed here so the next person does
not rediscover it.

## `TariffServer` is a singleton that nothing declares to be one

`apps/telegram_bot/handlers/top_up_balance.py` reads the same table twice with
different strictness: line 101 takes `afirst()` to record the funnel step, line
113 takes `aget()` to price the invoice. Each is defensible on its own — the
funnel must never crash the screen, and `aget()` failing loudly on two rows is
**correct**, because `afirst()` there would silently bill at whichever row sorts
first and a wrong-price charge to a real customer is worse than a visible error.

Together they disagree. With two rows, the funnel records a plan at the first
row's price and the invoice then refuses to pick one, so the customer taps an
amount and gets an error while analytics believes a plan was chosen. With zero
rows, `aget()` raises before `send_invoice` and the customer gets nothing.

The checkout probe now catches both — `tariff_missing` and `tariff_ambiguous`
are separate verdicts and are blamed before the payment provider is — so this is
observable rather than silent. But monitoring a constraint is not enforcing one.

**The fix is in the data, not the handler:** a uniqueness constraint plus a
migration, after which both call sites can read the same way. Not done because a
schema change on a live money path is its own decision.

## Rotating the panel credential — the script would break the panel

The 3x-ui credential must be treated as disclosed: it sat in a mode-644 `.env`
behind an internet-reachable service until 2026-08-13. Rotating it is right.
**`ops/scripts/rotate_special_xui_credentials.sh` cannot do it as written**, and
that was found by reading it rather than by running it:

1. **It rotates `webBasePath` and never touches nginx.** nginx fronts the panel
   with `location <secret path> { proxy_pass http://127.0.0.1:23133; }`. After
   rotation x-ui answers on the new path while nginx forwards only the old one,
   so the panel becomes unreachable over HTTPS and the bot — whose `vpn_url` the
   script does update — gets a 404 from nginx. Customer tunnels survive, because
   xray never consults the panel; issuing, revoking and expiry sync do not.
   The script predates nginx fronting the panel.
2. **It runs `x-ui restart`.** That drops every VLESS session on inbounds 5 and
   10 at once — all paying customers plus the other tenant. Clients reconnect in
   seconds, but it is visible and it is not mentioned anywhere in the script's
   output.

**Before running it:** teach it to rewrite the nginx `location` to the new path
and reload (`nginx -t` first, which fails closed on a bad config), or leave
`webBasePath` alone and rotate only the username and password. Rotating the
credential is the security-critical half; rotating the path is defence in depth
and is what pulls nginx in.

**Deferred by owner decision, 2026-08-13, to a quiet hour.** The reason is the
`x-ui restart`, not the credential: at the time of the decision inbound 5 was
carrying roughly 180 MB/min, so every paying customer was mid-session. Nothing
about the exposure changed — the credential is still to be treated as disclosed,
and the service that held it is firewalled rather than removed. Do this before
assuming the panel is safe.

## A region is picked without ever checking the endpoint is alive

Selection takes the host belonging to the fewest regions and excludes the public
resolvers. It never dials anything. Whether the chosen server answers is not an
input, and `docs/MIRROR-INBOUNDS-SPEC.md` already states the opposite intent
under "Safety constraints": *every rendered line must be tested before going
live; a broken link is worse than no link.*

Measured 2026-08-13 by probing all sixteen servers in the configured provider's
document rather than only the nine we render: **two are dead** —
`BRIDGE_RUSSIA_VLESS_1` (`213.171.9.195`) and `L3_BRIDGE_VLESS_1`
(`78.159.245.59`), both Russian. The Russian region alone offers six candidates,
two of them dead. We rendered a working one, and nothing in the code made that
happen.

**What it costs when it goes wrong:** a customer taps a country, the client
opens a TCP connection that succeeds or times out, and nothing works. It reads
as our fault. This is the same failure shape as the `1.1.1.1` incident, where
every region resolved to a Cloudflare resolver — that one was caught because it
hit every region at once. One dead country would not be.

**The fix is a liveness signal in selection**, not a bigger exclusion list: probe
candidates out of band, cache the verdict, and prefer a host known to answer.

**Live since 2026-08-14.** `probe_mirror_liveness` dials every endpoint a
configured source offers and writes `MirrorEndpointLiveness` rows; selection
reads them behind `SUBSCRIPTION_BACKUP_LIVENESS_ENABLED`, now `true` on BOT.

The first real run reproduced, independently, the two dead nodes an operator had
found by hand — `213.171.9.195` and `78.159.245.59` — out of 15 probed. It
counts a node alive only when **bytes come back through the tunnel**: the decoys
accept the TCP connection and reset the Reality handshake, so a port check would
call all nine of them healthy.

**Scheduled by `special-mirror-liveness.timer` on BOT**, every 30 minutes,
`Persistent=true`, running the command through `docker exec`. It is a management
command and not a Celery task on purpose — the workers are `--pool=solo` and a
60-second dial would block the queue, and the `monitoring` worker's own checks
run every minute. Verified by starting the service by hand:
`Result=success`, 15 verdicts written.

**The interval and the expiry are a pair.** Verdicts stop counting after
`SUBSCRIPTION_BACKUP_LIVENESS_MAX_AGE_SECONDS` (3600), so a 30-minute cadence
survives one missed run and goes blind after two — deliberately, because falling
back to the old blind selection is safer than trusting a measurement of unknown
age. That fallback is not theoretical: verdicts written at 23:12 UTC had expired
by the next morning, and the renderer duly served a node the prober had marked
dead. Nothing was broken; the schedule simply did not exist yet. **If a country
you expect is missing, or a dead one appears, read `checked_at` before reading
the code.**

## Every Russian address in the provider document is an exit, not a bridge

Recorded because the tag names invite the opposite conclusion. The document
carries seven Russian servers out of sixteen and names several of them
`BRIDGE_*`, `L1_BRIDGE_*`, `L2_BRIDGE_*`, `L3_BRIDGE_*`, with `L1`/`L2` using a
Reality SNI of `id.x5.ru` and `L3` `media-newportal.x5.ru` — a large Russian
retailer, which is ordinary DPI camouflage for a Russian ingress.

Probed end to end anyway. **No outbound uses sing-box `detour`**, so there is no
client-side chaining at all, and every live node exits in the country it is
entered from. The `BRIDGE_<country>` entries are byte-identical twins of their
plain counterparts — same address, same exit. The one real chain is
`L2_BRIDGE` (`91.240.87.119`) exiting at `L1_BRIDGE` (`194.26.229.158`), and
both ends are in Russia.

So no mirrored endpoint is relayed. What the `x5.ru` SNI does mark is something
else: an endpoint that stays reachable while a region is cut to a whitelist.
The endpoint flag is now named `whitelisted` for that reason and is set only by
`SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES`, never by a tag. With the default
empty list, `белые списки` still marks exactly one line: ours.

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
