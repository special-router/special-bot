# Bringing a provider's inbounds into our subscription

[`MIRROR-INBOUNDS-SPEC.md`](MIRROR-INBOUNDS-SPEC.md) says what the feature is.
This is what it took to make one real provider work, in order, with the traps
that cost time. Every step below produced a wrong answer at least once before it
produced a right one, and the wrong answers are recorded because each of them is
reproducible by the next person.

First provider ingested **2026-08-13**: a Remnawave-based service, 9 regions,
reached through one operator-owned subscription URL. `UserVPN` 801 only.

---

## 0. What the shape of this actually is

We do not import the provider's *inbounds*. There is nothing to import: an
inbound is a listener on their host, and we have no access to it. What we import
is their **subscription document** — the same one their own app fetches — parse
each outbound out of it, and re-emit it as a line in our document under our own
label. The tunnel is theirs end to end; we are a directory of it.

This is why the whole thing lives in `apps/subscriptions/views.py` and not in
`utils/py3xui/`, and why "push their inbounds into our 3x-ui" is not a thing that
can be done: 3x-ui owns listeners, not remote credentials.

---

## 1. The secret file, and the `.env` trap that ate an hour

The subscription URL is bearer access. It goes in a host file, never in Git,
never in `.environment`:

```bash
# on BOT, as root
install -m 600 /dev/null /etc/special-bot/subscription-backup.json
# then write: {"upstream_urls": ["https://<provider>/<token>"]}
```

Compose binds that path into `web`. **The path is read from
`/root/special-bot/.env`, not from `.environment`.** Two different files with
confusingly similar jobs:

| File | Read by | Holds |
|---|---|---|
| `/root/special-bot/.environment` | the container, as `env_file` | every Django setting |
| `/root/special-bot/.env` | `docker compose` itself, for `${...}` interpolation | only what the Compose file substitutes |

`SUBSCRIPTION_BACKUP_SECRET_HOST_PATH` is interpolated by Compose, so putting it
in `.environment` leaves it empty, Compose falls back to `/dev/null`, settings
correctly rejects the non-regular file, and the feature is off while every flag
looks right. The symptom is silence — no error anywhere, just three lines in the
subscription. If mirrors do not appear, check this before touching anything else:

```bash
docker inspect special-bot-web-1 --format '{{range .Mounts}}{{.Source}} {{end}}' | tr ' ' '\n' | grep -i backup
```

`/dev/null` there means `.env` is missing the path.

---

## 2. Identify as a client, or get an instruction leaflet

Asked with no device identity, the provider answered with three **plaintext**
`vless` outbounds whose tags were a message to a human — «Приложение не
поддерживается», «Поддерживаемые приложения:», «Happ, V2RayTun, INCY, Koala
Clash» — plus `x-hwid-not-supported: true` and `x-hwid-limit: true`.

I read that as "this provider only serves plaintext, it is junk". That was
wrong, and it is the single most expensive mistake in this feature: the document
was not a configuration at all, it was a leaflet telling us to use a real client.

A Remnawave provider decides what to serve from the *device*, not just the
`User-Agent`. Send both:

```dotenv
SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT=SFI/1.9
SUBSCRIPTION_BACKUP_UPSTREAM_HWID=<stable per-installation id>
SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS=Android
```

`x-hwid` must match `^[a-zA-Z0-9=-]{10,64}$`; anything else and the code sends no
identity headers at all — silently, by design, because a malformed identity is
worse than none. The `User-Agent` picks the *format*: `SFI/1.9` → sing-box JSON,
`v2rayNG/1.8.5` and `Happ/1.0` → v2ray array, `clash-verge/1.5` → YAML. Changing
it changes which parser runs.

With both, the same URL returned `x-hwid-active: true` alone, ~113 KB, 9 regions.

### Never probe with a throwaway HWID

The provider's plan allows **one device**. An ad-hoc `curl` with an invented
`x-hwid` takes the slot, and then the configured HWID is refused — «Вы превысили
лимит устройств: 1/1». That looked exactly like a credential problem and was not.

If you must probe by hand, probe with the value already in `.environment`. If a
slot has already been burned, the recovery is to set the configured HWID to
whichever value currently holds it — or to raise the limit in the provider's
cabinet, which is where this still stands.

One stable value for the whole installation, not per user and not per request: a
per-user value spends a device slot on every subscription refresh and reads to
the provider as a device flood.

---

## 3. Parsing: the two bugs that dropped every endpoint

**Reality `short_id` is optional.** Our predicate required both `pbk` and `sid`:

```python
if security == 'reality' and not (endpoint['public_key'] and endpoint['short_id']):
    return None
```

The provider omits `sid` on every server. All 31 endpoints were parsed correctly
and then discarded by this line — again with no error, just three lines out. Only
`public_key` is mandatory.

**The caps were sized for a toy source.** A real multi-region document is ~113 KB
with ~80 servers, and the sing-box format interleaves servers with the selector
groups that name their regions, so the entry count is roughly double the server
count. Raised: per-source endpoints 64 → 128, `AGGREGATE_MAX_LINES` 128 → 256,
parse bound 512. The byte caps, the 8 s deadline and the 300 s cache TTL were
re-checked against the real document and held unchanged.

---

## 4. Which server a region gets — and the `1.1.1.1` incident

The first selection took the alphabetically first candidate per region. The
provider offers `1.1.1.1` as its `🇪🇺 Fastest` node, and `1.1.1.1` sorts first
everywhere, so **every rendered region pointed the customer at a Cloudflare
resolver**. TCP connect succeeds, the Reality handshake can never complete, and
the whole subscription reads as dead. That is what "ничего не работает" was.

Two rules now:

- `_MIRROR_EXCLUDED_HOSTS` — Cloudflare, Google, Quad9, Yandex, OpenDNS — are
  never rendered, whatever the provider labels them. A region whose every
  candidate is excluded is **dropped**; a missing country is honest, a country
  that silently fails is not.
- Among the rest, pick the host belonging to the **fewest** regions. A host
  listed under nine flags is a front or an aggregate, not the server of any one
  country; a host under a single flag is what that flag names.

Ordering is by rendered label, then by how many regions the host serves, then by
`(host, port)` — every key a property of the endpoint set, never of a position in
the provider's document, so a provider reordering its outbounds cannot reshuffle
a customer's list.

---

## 5. Labels: none of the provider's bytes reach the customer

`→ Remnawave` names the service and the panel software; `🇸🇪 SWEDEN_VLESS_1` is
their rack inventory. Rendering either tells our customer who we buy from. The
label is read for exactly one thing — an ISO 3166-1 alpha-2 code, from a flag
emoji or a place written in words — and the rendered text, flag included, is
composed here from that code. No country signal → `🌐 Backup`.

---

## 6. Verify with a real handshake, not with a TCP connect

`ops/scripts/probe_mirror_tunnel.py` renders subscription 801, starts a
throwaway xray per line with a local SOCKS inbound, and fetches an echo service
through it. Run it inside `special-bot-web-1`; it needs the `xray` binary and
`PySocks`.

Two properties of that script are not decoration:

- **It carries our own NL Direct line as a control.** The first version of this
  probe reported all 8 provider endpoints DEAD. The endpoints were fine —
  `urllib` silently ignores a SOCKS proxy handed to it, so every request went out
  over the host's own network and the test measured nothing. An instrument that
  cannot pass a known-good endpoint proves nothing about the ones under test.
- **It reads the egress IP, not just "connected".** Distinct egress IPs per line
  are the proof that the tunnel is actually carrying traffic to a different
  place. A resolver answers a TCP connect happily.

Final state: 10/10 OK, distinct egress per region.

---

## 7. Rolling out

```dotenv
SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED=true
SUBSCRIPTION_BACKUP_TEST_USER_IDS=[801]
```

The allowlist is the whole safety mechanism. Widening it to everyone is **not**
a flag decision, because of two properties of this arrangement that no amount of
code fixes:

1. **Every customer shares one upstream UUID.** We cannot revoke one person, we
   cannot attribute traffic, and one abuser gets the account killed for all of
   them at once.
2. **The device limit is the provider's, not ours.** At 1/1 the account is a
   single seat; our subscription refreshes and the operator's own client compete
   for it.

Neither is a reason never to widen it — they are the two things to have an answer
for first. Per-user upstream credentials, or chaining the provider through our
own egress, are the two shapes that remove them.

## Reference

- Renderer and parsers: `apps/subscriptions/views.py` — `_fetch_upstream_payload`,
  `_structured_upstream_links`, `_singbox_endpoints`, `_v2ray_endpoints`,
  `_normalized_mirror_endpoint`, `_build_mirror_vless`, `_is_identity_placeholder`
- Probe: `ops/scripts/probe_mirror_tunnel.py`
- Settings: `SUBSCRIPTION_BACKUP_*` in [`FLAGS.md`](FLAGS.md)
- Commits: `3507934` client identity, `7c8676a` optional short id, `62ca6e0`
  branding and per-region cap, `e8be33c` real-server selection
