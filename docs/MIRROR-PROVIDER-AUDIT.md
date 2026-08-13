# Auditing a provider's subscription document

What the configured provider actually ships, measured on **2026-08-14** by
fetching the document with the configured device identity and dialling every
node in it. Written down because the shape repeats across providers and because
three of these findings changed what our renderer does.

Reproduce with `ops/scripts/probe_mirror_document.py`, which probes every server
in the document rather than only the ones we render.

---

## The shape

| | |
|---|---|
| Format | sing-box JSON (chosen by `User-Agent: SFI/1.9`) |
| Size | ~12 KB |
| `outbounds` | 33 — 31 `vless`, 1 `selector`, 1 `direct` |
| Distinct servers | 16 |
| Selector groups | **one**, `→ Remnawave`, listing all 31 flat |
| `urltest` groups | **none** |

There is no latency-based group. Anything named "Fastest" is an ordinary
outbound with a name, not a measurement.

## Eleven of the 31 outbounds do not work

**Nine are decoys.** `🇪🇺 Fastest`, `🇳🇱 Netherlands`, `🇩🇪 Germany`, `🇸🇪 Sweden`,
`🇳🇴 Norway`, `🇺🇸 USA`, `🇰🇿 Kazakhstan`, `🇯🇵 Japan` and `🇷🇺 Russia` all point at
**`1.1.1.1:443`** — Cloudflare's resolver. Probed: `ConnectionResetError`. The
TCP connect succeeds and the Reality handshake cannot complete, which is the
worst failure shape there is, because a client shows the endpoint as reachable.

These carry the **cleanest names in the document** — a plain country, no
`_VLESS_1` suffix — so they are exactly what a person scrolling a list picks
first. They are also the ones our `_MIRROR_EXCLUDED_HOSTS` rule drops, which is
the whole reason that rule exists: an earlier ingest picked `1.1.1.1` for every
region and the entire subscription read as dead.

A tell worth remembering: the decoys carry `utls.fingerprint: chrome` while
every real node carries `firefox`, and `🇪🇺 Fastest` alone has
`tls.server_name: "1.1.1.1"` — an IP where a domain belongs. Different
generator, different code path.

**Two more are simply down**, both Russian: `BRIDGE_RUSSIA_VLESS_1`
(`213.171.9.195`) and `L3_BRIDGE_VLESS_1` (`78.159.245.59`).

So 22 outbounds over 13 working servers.

## `BRIDGE_` means nothing observable

Eight outbounds are named `BRIDGE_<country>_VLESS_1`. Each is a **byte-identical
twin** of its plain counterpart: same address, same port, same SNI, same key —
and the probe returns the same exit address. Whatever the distinction is, it
lives inside the provider and is invisible from outside.

**No outbound uses sing-box `detour`**, so there is no client-side chaining
anywhere in this document. Every live node exits in the country it is entered
from. The single real chain is `L2_BRIDGE` (`91.240.87.119:59406`) exiting at
`L1_BRIDGE` (`194.26.229.158`) — both ends in Russia.

Conclusion: **no mirrored endpoint is relayed.** Our `relayed` flag being
`False` in every parser is a measured fact, not an untested default.

## The whitelist bypass is an SNI, not a route

The subscription advertises bypass of Russian mobile-internet shutdowns. It is
not in the routing config — `route` has exactly three rules (sniff, hijack DNS,
private addresses direct) and no domain logic at all, and `dns` is an ordinary
fakeip setup pointed at `1.1.1.1`. Nothing there treats one destination
differently from another.

It is in `tls.server_name`:

| SNI | Nodes | What it is |
|---|---|---|
| `cloudrynth.com` | 27 | the provider's own camouflage domain |
| `id.x5.ru` | 2 | `L1_BRIDGE`, `L2_BRIDGE` — **both alive** |
| `media-newportal.x5.ru` | 1 | `L3_BRIDGE` — dead |
| `1.1.1.1` | 1 | `🇪🇺 Fastest`, the decoy |

`x5.ru` is a large Russian retailer, the kind of domain that stays reachable
when a region's mobile internet is cut to a whitelist so that payments and
shops keep working. A Reality connection announcing that SNI passes inspection
that blocks everything else.

**The exit is Russian.** These nodes restore *access*, not a foreign address —
`L1` exits at its own Russian IP. Sold as "a foreign IP around blocks" it would
be a lie; sold as "the internet works when it is being jammed" it is accurate.

Our URI builder already carries `sni` through (`_build_mirror_vless`), so a
rendered bypass node would keep working for a customer. We do not render one:
all Russian nodes share the 🇷🇺 flag and compete for a single per-region slot,
which an ordinary Russian exit won.

## One key for the whole fleet

Every one of the 31 outbounds carries the **same Reality public key**. One
keypair covers every country, every node, decoys included. Nine distinct client
UUIDs are spread across them.

For a customer that is invisible. For an operator it means the nodes are one
deployment wearing sixteen addresses, and that a single compromised private key
ends every endpoint in the document at once — including the ones we resell.

Everything else is minimal: no `flow` (so no `xtls-rprx-vision`), no
`short_id`, no multiplex, no transport settings. Plain VLESS over Reality with
uTLS, and nothing to negotiate.

## What this changed on our side

- `_MIRROR_EXCLUDED_HOSTS` is not a nicety. Nine of 31 outbounds resolve to a
  public resolver and they hold the best names.
- Selection still never dials *inside a request*, but it no longer picks blind:
  `probe_mirror_liveness` dials out of band and selection drops a candidate
  carrying a fresh dead verdict. Default off; see
  [`OPEN-ITEMS.md`](OPEN-ITEMS.md#a-region-is-picked-without-ever-checking-the-endpoint-is-alive).
  With 11 dead outbounds in one document, picking a live one was luck.
- Bypass endpoints cannot be recognised from a tag name, so the signal is
  `SUBSCRIPTION_BACKUP_WHITELIST_SNI_SUFFIXES` — a configured SNI suffix an
  operator declares — and never the word `BRIDGE`, which this document proves
  means nothing. A matching endpoint renders as its own `белые списки` line
  outside the per-region cap, keeping its Russian exit flag.
- Geolocating a shared IP block needs two sources. One service placed four
  servers in Germany; a second placed them in Sandefjord, Frankfurt, Stockholm
  and Amsterdam, matching the flags. The first returned an empty ASN for that
  block, which is what exposed the guess.
