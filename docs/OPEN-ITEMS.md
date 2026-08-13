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

### Internal same-origin canary

`SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED=false` and
`SUBSCRIPTION_INTERNAL_MEMBERSHIP_SYNC_ENABLED=false`. Restricted to `UserVPN`
801 and retained inbounds 7/9/13 (TCP) plus 10 (gRPC on public `:80`).

**Blocked on:** an owner decision. The earlier blocker — a plaintext-HTTP panel
control plane — is **resolved**: the panel is HTTPS-only and
`utils/py3xui/async_api.py` refuses anything else. Enable membership sync before
rendering, never the other way round.

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
| `3000` | An unidentified service calling itself `darkcore-connection-service`. Nobody has established what it is or who owns it. Identify before closing. |
| `8080` | Backend for the gRPC canary path; reachable directly today. Diagnostic-only and never advertised, but still exposed. |
| `8443` | The primary VLESS/Reality inbound. Not closable — it is the product. |
| `27914` | A retained alternate inbound used by the internal canary set. |

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
