# Settings and rollout state

Every environment-backed setting in `bot/settings.py`, in one place. This file
is checked mechanically: `ops/scripts/validate_repository.py` fails if a name
here does not exist in `bot/settings.py`, or if a setting there is missing here.
Adding a setting without a row is a failing build, not a documentation debt.

**Column meaning.** *Default* is what `bot/settings.py` uses when the variable is
absent — this is repo truth. *Prod* is what the running `.environment` on BOT is
reported to hold as of **2026-08-12**; the repository cannot verify it, so `?`
means "read the file on the host before assuming". A default and a production
value differing is normal and is the whole reason this column exists.

Shipped-but-inert features are listed together in
[`OPEN-ITEMS.md`](OPEN-ITEMS.md).

## Django core

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `SECRET_KEY` | str | `django-insecure-local-development-only` | ? | Django signing key. The default is a development placeholder and must never run in production. |
| `DEBUG` | bool | `False` | `false` | Django debug mode. |
| `ALLOWED_HOSTS` | list | `localhost,127.0.0.1,0.0.0.0,sub.special-wifi.ru` | ? | Host header allowlist. The subscription hostname is in the default because NL proxies to BOT with that `Host`. |
| `DATABASE_URL` | db url | `postgres://vpnbot:vpnbot@:5432/vpnbot` | ? | Tests override this with `sqlite:///:memory:`. |
| `TIME_ZONE` | str | `UTC` | ? | Read only to set `CELERY_TIMEZONE`; Django's own `TIME_ZONE` is hardcoded `UTC`. |
| `DJANGO_LOG_LEVEL` | str | `INFO` | ? | Root logger level. `httpx`, `httpcore`, `py3xui` and `urllib3` stay pinned at `WARNING` regardless — they log the secret panel path at `INFO`. |

## Telegram and payments

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | str | empty | set | Bot API token. |
| `YOUMONEY_TOKEN` | str | empty | set | Payment provider token. |
| `BOT_LINK` | str | `https://t.me/SpecialVPNbot` | ? | Referral link base. |
| `SUPPORT_CHAT_ID` | int | `0` | `0` | Operators' forum supergroup. **Zero registers no support handler at all** — the menu button stays a plain link and the bot does not read private text messages. Enabling requires two manual Telegram steps first; see [`OPEN-ITEMS.md`](OPEN-ITEMS.md#support-tickets). |
| `ADMIN_BASE_URL` | str | `https://sub.special-wifi.ru/admin/` | ? | Where the «Карточка клиента» button in a support topic points. Only the scheme and host are used: the path comes from `reverse('admin:users_telegramuser_change')`, so moving the admin off `/admin/` does not break the button. Empty removes the button entirely — Bot API rejects a whole keyboard over one invalid URL, and that would take the close button with it. |
| `TELEGRAM_BUTTON_ICONS_ENABLED` | bool | `False` | `false` | Premium `icon_custom_emoji_id` on inline buttons. Bot API accepts the field only for a bot whose owner holds Telegram Premium; without it the whole keyboard is rejected, so off is the only safe default. |

## Redis and Celery

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `REDIS_HOST` | str | `localhost` | ? | Used only to assemble `REDIS_URL`. |
| `REDIS_PORT` | int | `6379` | ? | As above. |
| `REDIS_DB` | int | `0` | ? | As above. |
| `REDIS_PASSWORD` | str | empty | set | Empty yields a passwordless URL, appropriate only for tests. |
| `REDIS_URL` | str | assembled from the four above | set | Broker and result backend. |
| `CELERY_ALWAYS_EAGER` | bool | `False` | `false` | Tests set it true. |

## Product limits

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `REFERRAL_PERCENT` | int | `30` | ? | Referral share. |
| `ANALYTICS_EVENTS_ENABLED` | bool | `True` | ? | Writes the append-only analytics event log. On by default because the log can never fail a money write — it is scheduled with `transaction.on_commit` and swallows its own exceptions. Turning it off loses nothing permanently: `backfill_money_events` reconstructs the same rows under the same idempotency keys. See [`ANALYTICS.md`](ANALYTICS.md). |
| `BALANCE_SPLIT_UI_ENABLED` | bool | `True` | ? | Whether the balance and profile screens show the «в том числе бонусных» line under the total. The total itself never changes with this flag: it is `annotate_balance()` either way. Off removes the line and the extra ledger pass per render; the operator report and admin still show the split. See [`ANALYTICS.md`](ANALYTICS.md#реальные-деньги-и-бонусы). |
| `MAX_KEYS` | int | `3` | ? | Maximum subscriptions per account. |
| `LIMIT_IP` | int | `2` | ? | `limit_ip` written into the 3x-ui client. Not enforceable on this deployment — xray sees only the SNI proxy's address — which is exactly why device binding exists. |

## Subscription rollout

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `SUBSCRIPTION_CONNECTOR_ENABLED` | bool | `False` | `true` | Allows creating/assigning `subId` in 3x-ui. `prepare_xui_subscriptions --apply` refuses without it. |
| `SUBSCRIPTION_DELIVERY_ENABLED` | bool | `False` | `true` | Makes the bot issue subscription URLs instead of direct `vless://` keys, and registers the `/subscription` handler. Off falls back to the stored direct key. |
| `SUBSCRIPTION_BASE_URL` | str | falls back to `SUB_URL`, then empty | set | Public subscription base. `subscription_proxy` takes the Direct hostname from it. |
| `SUB_URL` | str | empty | ? | Legacy alias, read only as the fallback for `SUBSCRIPTION_BASE_URL`. |
| `SUBSCRIPTION_DIRECT_ADVERTISED_PORT` | int | `0` | ? | Port advertised for NL Direct. Zero advertises the inbound's own port. Exists because xray may bind privately behind the shared SNI-routed `:443`. |
| `MIRROR_INBOUND_IDS` | json | `[]` | `[14]` | Inbound ids whose client `enable`/`subId` state mirrors the primary inbound. Add/remove/enable/disable propagate to every id listed. |
| `STATUS_INBOUND_ID` | int | `0` | `1` | Inbound carrying the per-client status label in its `email` field. Working inbounds keep `email` empty so the subscription remark stays clean. Zero disables it. |
| `SUBSCRIPTION_STATUS_ENTRY_ENABLED` | bool | `True` | ? | Whether the first subscription line stays the non-working `127.0.0.1:1` entry whose remark carries «осталось N дней». True keeps today's three-line contract. Turn it off only after a real client is observed rendering `subscription-userinfo`; for a client that ignores headers this entry is the only place the term appears. |
| `SUBSCRIPTION_PROFILE_TITLE` | str | `SPECIAL VPN` | ? | Profile name shown inside the client app, sent as `profile-title: base64:<…>`. Empty omits the header. |
| `SUBSCRIPTION_SUPPORT_URL` | str | `https://t.me/Special_Wifi_Official` | ? | Destination behind the client app's support button (`support-url`). Must be a plain URL; a control character in it drops the header rather than failing the response. |
| `SUBSCRIPTION_ANNOUNCE_TEXT` | str | empty | ? | Banner the client app shows above the profile, sent as `announce: base64:<…>`. Empty omits the header, so there is no "no news" banner. Bounded at 512 characters. |
| `SUBSCRIPTION_RELAY_HOST` | str | empty | — | **Dead setting.** Nothing outside `bot/settings.py` reads it; the Relay endpoint comes from `Server.client_vpn_host`. |
| `SUBSCRIPTION_RELAY_PORT` | int | `443` | — | **Dead setting**, same as above. |

## Device binding

Rollout phase lives here. See [`OPEN-ITEMS.md`](OPEN-ITEMS.md#device-binding-phase-2).

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `SUBSCRIPTION_DEVICE_LIMIT` | int | `2` | `2` | Distinct `x-hwid` values per subscription. `UserVPN.device_limit` overrides it per record. |
| `SUBSCRIPTION_HWID_STRICT` | bool | `False` | `false` | Refuse clients that send no usable identifier. Keep false until the fleet sends one. |
| `SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED` | bool | **`True`** | **`false`** | Whether binding a *new* device needs a window opened from the bot. **The code default is `true`; phase 1 is running only because `.environment` explicitly sets it false.** False is a launch state, never the steady state — it is what lets a leaked `sub_id` spend the slots. |
| `SUBSCRIPTION_DEVICE_BINDING_WINDOW_MINUTES` | int | `15` | ? | Window length. |
| `SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR` | int | `5` | ? | Ceiling on new bindings per subscription per hour, applied even inside a window, so freeing slots never becomes a flooding budget. |
| `SUBSCRIPTION_DEVICE_RESET_COOLDOWN_HOURS` | int | `1` | ? | Self-serve device reset cooldown. |

## External backup subscriptions

All default-off. Bearer URLs never appear in Git or in this repository's
environment; they come from a mode-0600 JSON file on the host.

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `SUBSCRIPTION_BACKUP_ENDPOINTS_ENABLED` | bool | `False` | **`true`** | Master gate for third-party endpoints in a subscription. |
| `SUBSCRIPTION_BACKUP_TEST_USER_IDS` | json | `[]` | **`[801]`** | Allowlist of `UserVPN.id` during rollout. |
| `SUBSCRIPTION_BACKUP_SECRET_FILE` | str | empty | container path | Path to the JSON secret inside the container. Compose binds `/dev/null` when the host path is unset, and settings rejects that non-regular file. Only a regular mode-0600 file is accepted. |
| `SUBSCRIPTION_BACKUP_UPSTREAM_HOSTS` | json | `None` | one provider host | Exact DNS hostname allowlist. Absent permits a controlled rollout; present but malformed denies everything. |
| `SUBSCRIPTION_BACKUP_CONNECT_TIMEOUT_SECONDS` | float | `3` | ? | Per-source connect timeout. |
| `SUBSCRIPTION_BACKUP_READ_TIMEOUT_SECONDS` | float | `5` | ? | Per-source read timeout. |
| `SUBSCRIPTION_BACKUP_RESPONSE_MAX_BYTES` | int | `262144` | ? | Per-source response cap. |
| `SUBSCRIPTION_BACKUP_CACHE_TTL_SECONDS` | int | `300` | ? | Upstream cache TTL. |
| `SUBSCRIPTION_BACKUP_MAX_SOURCES` | int | `8` | ? | Maximum configured sources. |
| `SUBSCRIPTION_BACKUP_AGGREGATE_MAX_LINES` | int | `256` | ? | Cap across all sources. Raised from 128 because one real multi-region provider document carries roughly 80 servers, so two sources would have been truncated silently. |
| `SUBSCRIPTION_BACKUP_AGGREGATE_MAX_BYTES` | int | `262144` | ? | Cap across all sources. |
| `SUBSCRIPTION_BACKUP_MAX_ENTRIES_PER_REGION` | int | `1` | ? | Mirrored endpoints kept per country, within one provider document. A provider lists every server it owns, so one region arrives nine times over; the customer picks a country, not a rack. Raise it to expose the rest. |
| `SUBSCRIPTION_BACKUP_MAX_MIRROR_ENTRIES` | int | `16` | ? | Mirrored lines kept across all sources, so a provider returning hundreds cannot bury our own status/Direct/Relay lines. The tighter of this and `SUBSCRIPTION_BACKUP_AGGREGATE_MAX_LINES` applies. |
| `SUBSCRIPTION_BACKUP_FETCH_DEADLINE_SECONDS` | float | `8` | ? | Deadline for the whole fetch phase of one request. |
| `SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT` | str | empty | `SFI/1.9` | Providers serve a different document per User-Agent and reject unknown ones (`SFI/1.9` → sing-box JSON, `v2rayNG/1.8.5` and `Happ/1.0` → v2ray array, `clash-verge/1.5` → YAML). Empty keeps the neutral agent, so upgrading does not change a configured source's format. |
| `SUBSCRIPTION_BACKUP_UPSTREAM_HWID` | str | empty | set, never printed | Device identifier sent as `x-hwid`. A provider running the Remnawave device convention serves an instruction document instead of a configuration without it. Must match `^[a-zA-Z0-9=-]{10,64}$`; anything else sends no identity headers at all. **One stable value per installation** — a per-user or per-request value spends a device slot per subscription refresh and reads as a device flood. Never commit a value. |
| `SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_OS` | str | empty | `Android` | Optional `x-device-os`, ≤32 printable ASCII. Sent only with a usable `SUBSCRIPTION_BACKUP_UPSTREAM_HWID`. |
| `SUBSCRIPTION_BACKUP_UPSTREAM_OS_VERSION` | str | empty | ? | Optional `x-ver-os`, ≤32 printable ASCII. Same condition. |
| `SUBSCRIPTION_BACKUP_UPSTREAM_DEVICE_MODEL` | str | empty | ? | Optional `x-device-model`, ≤64 printable ASCII. Same condition. |
| `SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS` | bool | `False` | `false` | Serve provider endpoints with neither TLS nor Reality. Off because plain VLESS carries the client UUID on the wire and is trivially fingerprinted — the same reason inbounds 11 and 12 were disabled. |

## Internal same-origin transport canary

Not a redundant mirror: every candidate is on the same NL origin.

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `SUBSCRIPTION_INTERNAL_INBOUNDS_ENABLED` | bool | `False` | `false` | Render canary lines for allowlisted users. |
| `SUBSCRIPTION_INTERNAL_MEMBERSHIP_SYNC_ENABLED` | bool | `False` | `false` | Synchronize enable/expiry for retained per-inbound memberships. Independent from rendering, and meant to be enabled first. |
| `SUBSCRIPTION_INTERNAL_TEST_USER_IDS` | json | `[]` | `[]` | Allowlist of `UserVPN.id`. Malformed JSON disables the canary rather than failing web startup. |
| `SUBSCRIPTION_INTERNAL_ENDPOINTS` | json | `[]` | `[]` | Entries carry only `inbound_id`, `advertised_port`, `label`. The renderer accepts only inbounds 7/9/13/10. |

## Monitoring

`SPECIAL_MONITOR_ENABLED`, `SPECIAL_MONITOR_L2_ENABLED` and
`SPECIAL_MONITOR_CHECKOUT_ENABLED` are read at import time to build
`CELERY_BEAT_SCHEDULE`, so changing any of them needs a beat restart.

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `SPECIAL_MONITOR_ENABLED` | bool | `False` | `true` | Schedules L0 (5 min), L1 (1 min) and Host capacity (5 min). |
| `SPECIAL_MONITOR_L2_ENABLED` | bool | `False` | `true` | Additionally schedules the protected L2 protocol probe. Requires `SPECIAL_MONITOR_ENABLED`. |
| `SPECIAL_MONITOR_CHECKOUT_ENABLED` | bool | `False` | `false` | Additionally schedules the checkout layer every 15 min. Three verdicts, all kept in `details`: the tariff lookup the top-up handler makes before it can bill anyone (`tariff_missing`, `tariff_ambiguous`), one `create_invoice_link` against the live provider token, and the days-since-cash-in gap. Ours is blamed before the provider's. Requires `SPECIAL_MONITOR_ENABLED`. Off means the task records nothing at all, so no stale green state is left behind. |
| `SPECIAL_MONITOR_CHECKOUT_TIMEOUT` | int | `15` | ? | Per-phase Bot API timeout in seconds; the probe's own deadline is twice this, because the per-phase timeouts cannot bound a call that stalls between phases and workers run `--pool=solo`. |
| `SPECIAL_MONITOR_CHECKOUT_AMOUNT` | int | `10000` | ? | Face value of the probe invoice, in kopecks. Nobody pays the link, but the provider validates the amount when issuing it. Too small and the layer opens with `invoice_amount_rejected`, which means raise this number — not `provider_token_rejected`, which means the token on BOT is wrong. |
| `SPECIAL_MONITOR_CASH_GAP_DAYS` | int | `3` | ? | Days without a `MoneyEvent` carrying `cash_amount > 0` before the checkout layer opens `cash_gap`. No cash-in event at all is never a gap — on an unseeded database that is indistinguishable from a stall. |
| `SPECIAL_MONITOR_FAILURE_THRESHOLD` | int | `2` | ? | Consecutive failures before an alert opens. `entitled_missing > 0` opens immediately. |
| `SPECIAL_MONITOR_PROBE_REGION` | str | `ru-bot` | ? | Label recorded with L1 results. |
| `SPECIAL_MONITOR_PAGING_ENABLED` | bool | `False` | `false` | Provider-neutral HTTPS webhook for opened/recovered transitions. Blocked on a destination and an accountable owner. |
| `SPECIAL_MONITOR_PAGING_WEBHOOK_URL` | str | empty | empty | Paging destination. |
| `SPECIAL_MONITOR_PAGING_OWNER` | str | empty | empty | Accountable on-call owner recorded with the page. |
| `SPECIAL_MONITOR_PAGING_TIMEOUT` | int | `10` | ? | Webhook timeout in seconds. |
| `SPECIAL_MONITOR_MIN_AVAILABLE_MB` | int | `128` | ? | Host-capacity floor for `MemAvailable`. |
| `SPECIAL_MONITOR_MIN_SWAP_MB` | int | `512` | ? | Expected swap. BOT has a persistent 1 GiB swapfile. |
| `SPECIAL_MONITOR_MAX_LOAD_PER_CPU` | float | `4.0` | ? | Load ceiling per CPU. |
| `SPECIAL_MONITOR_MAX_OOM_KILLS` | int | `0` | ? | Any kernel OOM kill is a failure. |
| `SPECIAL_MONITOR_ENDPOINTS` | json | `[]` | set | L1 endpoint matrix — non-secret labels only. |
| `SPECIAL_MONITOR_EXPECTED_INBOUNDS` | json | `[]` | set | L0 expected inbound properties. |
| `SPECIAL_MONITOR_CANARY_USER_VPN_ID` | int | `0` | set | The single `UserVPN` used for L2. Zero fails L2 closed as `not_configured`. |
| `SPECIAL_MONITOR_SERVER_ID` | int | `1` | ? | `Server` row the monitors read. |
| `SPECIAL_MONITOR_XRAY_PATH` | str | `/usr/local/bin/xray` | same | Xray binary inside the monitoring container. |
| `SPECIAL_MONITOR_EXPECTED_EGRESS` | str | empty | set | Egress address L2 must observe. Missing or invalid fails closed. |
| `SPECIAL_MONITOR_HEALTH_URL` | str | `https://api.ipify.org` | same | L2 egress check target. Must be HTTPS and carry no credentials. |

## 3x-ui control plane reads

| Setting | Type | Default | Prod | What it does |
|---|---|---|---|---|
| `XUI_CONTROL_PLANE_READ_ATTEMPTS` | int | `4` | ? | The panel can return a briefly incomplete client list, so reads repeat until two consecutive results agree. A single short read would otherwise look like missing entitlement. |
| `XUI_CONTROL_PLANE_READ_BACKOFF` | float | `1.5` | ? | Backoff between those attempts. |
