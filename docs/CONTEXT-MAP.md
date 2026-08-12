# Context map — what your task actually touches

One section per kind of change. Each lists the files that decide the behaviour,
the hosts involved, the settings that gate it, the command that proves it, and
the way this area has broken before. Start at the section that matches the
request; do not go looking for the rest.

Flags named here are defined in [`FLAGS.md`](FLAGS.md) with their production
values. Everything runs from the repository root.

---

## Billing and balance

**Files**

- `apps/subscriptions/tasks.py` — `update_user_vpn` (the daily task),
  `_charge_user` (all the decisions), `_charge_date` (idempotency key).
- `apps/users/querysets.py` — `annotate_balance()`.
- `apps/payments/models.py`, `choices.py`, `querysets.py` — `Transaction`,
  `TransactionSourceChoices.EVERYDAY_SYSTEM`.
- `apps/payments/migrations/0006_transaction_daily_charge_idempotency.py` — the
  partial unique constraint.
- `apps/servers/models.py` — `TariffServer.price`, reached as
  `user_vpn.server.tariff.price`.
- `apps/vpn/services/remove_vpn_user_from_server.py` — the panel-side disable.
- Tests: `apps/subscriptions/test_billing.py`.

**How it behaves today**

Balance is shared per account; the charge is per subscription, each in its own
nested `atomic()` block so one failing subscription does not roll back the
others. Subscriptions are funded oldest-first (`order_by('user_id',
'created_at', 'id')`); the first one the balance cannot cover, and every newer
one, is disabled — even if a newer one is cheaper. A charge never takes the
balance below zero: if the remainder is short, no transaction is created at all.
Idempotency is `UniqueConstraint(fields=('user_vpn', 'charge_date'),
condition=Q(source='EVERYDAY_SYSTEM'))`, and `_charge_date` shifts the day
boundary forward by `EARLY_START_TOLERANCE = 5 minutes` so a beat start a few
milliseconds before midnight does not write yesterday's key and skip a whole
day for every account.

The price is **database data**, not a constant. Production charges 7.00 ₽/day
per subscription because that is what the `TariffServer` row says.

**Hosts** BOT only, plus one 3x-ui call per disable.

**Flags** none. Billing has no kill switch.

**Validation**

```bash
env DJANGO_SETTINGS_MODULE=bot.settings DATABASE_URL='sqlite:///:memory:' \
  CELERY_ALWAYS_EAGER=true <venv>/python -m pytest apps/subscriptions/test_billing.py -q
```

**Risks**

- `annotate_balance()` sums transactions with **no status filter**, so PENDING
  and FAILED rows count. Nothing currently creates a non-SUCCESS transaction
  outside tests, so the two readings agree — but a filtered `Sum` written
  locally would make the charged amount disagree with the balance shown in
  `handlers/profile.py` and in the subscription status line.
- `select_for_update()` is a silent no-op on SQLite. The account lock in
  `_charge_user` is real on PostgreSQL and absent in tests; concurrency has to
  be reasoned about, not test-driven.
- One message per account per run, decided from the final state. Sending per
  subscription produced a warning and a shutdown notice in the same minute.

---

## Money flow analytics

**Files**

- `apps/analytics/taxonomy.py` — the only place that says what a `source` means
  economically, and the top-up bonus ladder inversion.
- `apps/analytics/balance_split.py` — real vs bonus pots derived from the same
  ledger, bonus consumed first. No new columns; `real + bonus` is always
  `annotate_balance()`.
- `apps/analytics/models.py` — `MoneyEvent`, `FunnelEvent`; `event_key` is unique.
- `apps/analytics/recording.py`, `signals.py` — the write path.
- `apps/analytics/backfill.py`, `reporting.py`, `management/commands/`.
- `apps/analytics/funnel.py` — one function per funnel step, and the list of
  call sites that now use them.
- Tests: `apps/analytics/test_taxonomy.py`, `test_recording.py`, `test_backfill.py`,
  `test_reporting.py`, `test_funnel_wiring.py`, `test_balance_split.py`.

**How it behaves today**

Additive only. Balance still comes from `annotate_balance()` over `Transaction`;
nothing here filters or reinterprets it. A `post_save` receiver on `Transaction`
schedules one `MoneyEvent` through `transaction.on_commit` and swallows every
exception, so an analytics failure cannot roll back a charge. `backfill_money_events`
rebuilds the same rows under the same keys, which is what makes losing a write
harmless. Full reasoning, and the list of questions history cannot answer, is in
[`ANALYTICS.md`](ANALYTICS.md).

Funnel steps are recorded from the bot handlers themselves. The step functions
are **synchronous and hit the database**, so every call site wraps them in
`sync_to_async`; without it Django raises `SynchronousOnlyOperation`, which
`record_funnel_event` then swallows and the step silently never lands.
`INVOICE_SENT` and everything after it stay at zero while `YOUMONEY_TOKEN` is
empty — that is the measurement, not a gap in the wiring.

The balance split is additive in the same sense: it recomputes both pots from
`Transaction` on every call and never writes. Bonus is consumed before real
money, an account with no grants is unchanged, and `real + bonus` equals
`annotate_balance()` by construction — `money_report` prints
`mismatched_accounts` so a divergence is loud rather than theoretical.

**Flags** `ANALYTICS_EVENTS_ENABLED`, `BALANCE_SPLIT_UI_ENABLED`.

**Validation**

```bash
env DJANGO_SETTINGS_MODULE=bot.settings DATABASE_URL='sqlite:///:memory:' \
  CELERY_ALWAYS_EAGER=true <venv>/python -m pytest apps/analytics -q
```

**Risks**

- Credit granted by hand (191k) is larger than money taken through the payment
  provider (126k). Adding the two produces a revenue number that is confidently
  wrong; `MANUAL` positive carries `cash_basis=UNKNOWN` for exactly that reason.
- A `YOUMONEY` amount is the payment *plus* a volume bonus of up to 30%. Treating
  it as cash overstates income by the size of the discount.
- The receiver fires for every `Transaction` insert, including the ~800 daily
  charges. It adds one row per money row; the kill switch removes even that.

---

## Subscription rendering and delivery

**Files**

- `apps/subscriptions/views.py` — `subscription_proxy` is the whole public
  endpoint; `_build_vless`, `_get_params`, `_reality_params`, `_endpoint`.
- `apps/vpn/services/subscription_delivery.py` — `get_user_access_url`, the bot
  side, with the direct `vless://` key as fallback on any exception.
- `apps/servers/subscription_connector.py` — `subId` creation/lookup in 3x-ui.
- `apps/subscriptions/management/commands/sync_expiry_times.py` — mirrors
  remaining days into panel `expiryTime` and the status label.
- Tests: `apps/subscriptions/test_views.py`.

**How it behaves today**

`GET /sub/<sub_id>` returns base64 of, in order: a non-working status line
(`127.0.0.1:1`, remark `📊 Подписка-осталось N дней` or `подписка окончена`),
`🇳🇱 NL Direct` on the subscription hostname, then — only for allowlisted test
users — internal canary lines and external backup lines, then `🇳🇱 NL Relay` if
`server.client_vpn_host` is set. `flow` is deliberately empty: forcing Vision
breaks the deployed client contract.

Response headers drive the client app's interface: `Profile-Update-Interval: 12`,
`subscription-userinfo` (whose `expire` is `days` from now, from the same
`balance // price` arithmetic as the status remark), `profile-title` and
`announce` as `base64:<…>`, `support-url`, `profile-web-page-url`,
`Cache-Control: private, no-store`, and the `x-hwid-*` set. `days` is clamped at
zero, so an overdrawn account expires now rather than in the past, and never
`expire=0`, which this format reads as unlimited. A configured value that could
not survive as a header is dropped, never raised — one bad character in
`.environment` must not become a 500 on every refresh.

The status entry is the reason `SUBSCRIPTION_STATUS_ENTRY_ENABLED` exists. Now
that `expire` carries the same term, the dead `127.0.0.1:1` line is redundant
for any client that reads headers; it stays on by default until a real client is
observed rendering it, because a client that ignores headers has nowhere else to
read the term.

`sync_expiry_times` runs at 00:05 UTC, after billing at 00:00 UTC.

**Hosts** BOT renders; NL nginx terminates TLS and proxies to BOT `:8001`.

**Flags** `SUBSCRIPTION_DELIVERY_ENABLED`, `SUBSCRIPTION_CONNECTOR_ENABLED`,
`SUBSCRIPTION_BASE_URL`, `SUBSCRIPTION_DIRECT_ADVERTISED_PORT`,
`STATUS_INBOUND_ID`, `MIRROR_INBOUND_IDS`, `SUBSCRIPTION_STATUS_ENTRY_ENABLED`,
`SUBSCRIPTION_PROFILE_TITLE`, `SUBSCRIPTION_SUPPORT_URL`,
`SUBSCRIPTION_ANNOUNCE_TEXT`.

**Validation**

```bash
env DJANGO_SETTINGS_MODULE=bot.settings DATABASE_URL='sqlite:///:memory:' \
  CELERY_ALWAYS_EAGER=true <venv>/python -m pytest apps/subscriptions/test_views.py -q
```

**Risks**

- Every existing test of `subscription_proxy` is a `SimpleTestCase` with
  `UserVPN.objects` and `TelegramUser.objects` patched and a `SimpleNamespace`
  standing in for the model. **`SimpleTestCase` blocks database access**, so any
  new ORM query on the default request path breaks the whole legacy contract
  suite. Gate new queries behind a condition those tests do not reach
  (`RequestFactory().get()` sends no custom headers) and put DB-backed tests in
  a separate `TestCase` file.
- The mocked subscription exposes only `id`, `enabled`, `server`, `user_id`,
  `vpn_uuid` — read anything else with `getattr(..., default)`.
- 3x-ui's own subscription endpoint cannot replace this view: it emits the first
  client UUID of the inbound for every subscriber.

---

## Device binding

**Files**

- `apps/subscriptions/devices.py` — `client_hwid`, `register_device`, the
  window/limit/rate logic. Returns booleans and durations, never an identifier.
- `apps/subscriptions/models.py` — `SubscriptionDevice`,
  `SubscriptionDeviceReset`, `SubscriptionDeviceBindingWindow`,
  `SubscriptionDeviceRegistrationRate`.
- `apps/subscriptions/views.py` — `_device_gate`, `_refused`.
- `apps/telegram_bot/handlers/bind_device.py`, `handlers/reset_devices.py`.
- Tests: `apps/subscriptions/test_devices.py`.

**How it behaves today**

Clients send `x-hwid` on every subscription refresh; the panel's own `limit_ip`
cannot be used because xray sees only the SNI stream proxy's address and keeps
no access log. A subscription binds up to `SUBSCRIPTION_DEVICE_LIMIT` (2)
distinct hwids, overridable per record by `UserVPN.device_limit`. Because the
endpoint is unauthenticated, binding a *new* hwid normally requires the account
holder to open a window from the bot, where Telegram signs who is asking. A
client that sends no usable hwid is served unless `SUBSCRIPTION_HWID_STRICT`.

This gates **config delivery, not the tunnel**: a client that already holds a
config keeps connecting.

**Flags** `SUBSCRIPTION_DEVICE_LIMIT`, `SUBSCRIPTION_HWID_STRICT`,
`SUBSCRIPTION_DEVICE_BINDING_WINDOW_REQUIRED` (the rollout phase switch),
`SUBSCRIPTION_DEVICE_BINDING_WINDOW_MINUTES`,
`SUBSCRIPTION_DEVICE_REGISTRATIONS_PER_HOUR`,
`SUBSCRIPTION_DEVICE_RESET_COOLDOWN_HOURS`. Rollout state is in
[`OPEN-ITEMS.md`](OPEN-ITEMS.md#device-binding-phase-2).

**Risks**

- A refusal must be byte-identical to an unknown `sub_id`: `_refused()` is the
  single 404 producer for exactly that reason. Adding a distinguishable
  response turns the endpoint into a `sub_id` oracle.
- `_HWID_PATTERN` is `[a-zA-Z0-9=-]{10,64}` and anything else counts as *no*
  identifier, so a malformed client cannot occupy a slot. Loosening it lets one
  client burn both slots.
- The hourly registration counter outlives the devices it counted, so a reset
  does not become a flooding budget.

---

## Panel, inbounds and xray

**Files**

- `utils/py3xui/async_api.py` — the fail-closed constructor;
  `utils/py3xui/async_api_inbound.py`.
- `apps/servers/vpn_client.py`, `apps/servers/internal_membership.py`.
- `apps/servers/management/commands/` — `audit_xui_inbounds`,
  `audit_xui_subscription`, `audit_xui_sub_id_coverage`,
  `validate_inbound_config`, `prepare_xui_subscriptions`.
- `ops/monitoring/inbound-matrix.json`.

**How it behaves today**

`AsyncApi.__init__` raises `ValueError('xui_https_required')` for any non-HTTPS
host and `ValueError('xui_tls_verification_required')` when verification is
switched off. There is no way to talk to the panel over plaintext from this
code. The panel lives behind nginx at a secret base path; its own port is
firewalled off. Control-plane reads are repeated until two consecutive reads
agree (`XUI_CONTROL_PLANE_READ_ATTEMPTS`), because 3x-ui returns briefly
incomplete client lists and a single short read would look like missing
entitlement.

**Hosts** NL. Panel credentials and URL live in the `Server` database row, not
in settings.

**Risks**

- Before disabling an inbound or a client, check for live traffic and
  ownership. 21 compatibility-only clients have no mapped owner and are never
  mutated.
- Never log a panel URL: the base path is equivalent to an access token.
- `subId` is absent from generated xray client objects. That is expected
  projection, not membership drift.

---

## Bot UI

**Files**

- `apps/telegram_bot/ui.py` — screen assembly; one conversation, one message,
  redrawn in place.
- `apps/telegram_bot/icons.py` — premium button emoji ids and mandatory
  fallbacks.
- `apps/telegram_bot/handlers/`, `apps/telegram_bot/inline_buttons/`,
  `register_handlers.py`.
- Tests: `test_ui.py`, `test_subscription_ui.py`, `test_utils.py`.

**Risks specific to testing handlers**

- Patch the **module-level model name** (`patch('...handlers.show_keys.UserVPN')`),
  not `.objects`. Every handler imports the same class, so patching `objects`
  patches the class and the last patch entered wins for all of them.
- `@override_settings` cannot decorate an `IsolatedAsyncioTestCase` class — it
  raises at import time and pytest reports a collection error, not a failure.
  Use it on methods, or use `django.test.TestCase`, which takes `async def
  test_*` directly and supports class-level `override_settings`.
- `icon_custom_emoji_id` is accepted by Bot API only for a bot whose owner holds
  Telegram Premium, and the code cannot see that status — hence
  `TELEGRAM_BUTTON_ICONS_ENABLED`, off. Custom emoji in message *text* is not
  possible at all for this bot and there is deliberately no helper for it.

---

## Support tickets

**Files** `apps/telegram_bot/support.py` (database only, no Bot API calls),
`apps/telegram_bot/handlers/support.py`, `SupportPrompt`/`SupportTicket` in
`apps/telegram_bot/models.py`, the operator's landing page in
`apps/users/admin.py`, tests in `test_support.py` and `apps/users/test_admin.py`.

**State** implemented and inert in this repository's defaults.
`register_handlers.py` registers no support handler while `SUPPORT_CHAT_ID` is 0,
so the menu button stays a plain link and the bot does not start reading private
messages for a disabled feature. Enabling needs a human: see
[`OPEN-ITEMS.md`](OPEN-ITEMS.md#support-tickets).

**How it behaves once the chat is set.** Photos, videos, documents, voice
messages and video notes cross in both directions, relayed by `file_id` — no
file content is read or stored. Text and attachment go as two separate Bot API
calls, text first, so a refused file cannot take the message with it; both the
customer's screen and the topic say so when a file does not arrive. Anything
else — stickers, GIFs, audio, contacts, locations, polls — is matched by the
handler's own filter purely so it can be refused out loud instead of reaching no
handler at all.

A ticket is claimed by the **first operator who replies**: `operator_telegram_id`
and `operator_name` land on the ticket row, and the topic is renamed to
`✅ Ticket #N | @user · Имя`. A second operator answering does not take it over —
their message is still signed with their own name for the customer, and who
wrote what inside the topic is Telegram's own display. The customer never sees a
numeric account id; an operator with neither name nor `@username` signs as
«Оператор».

The topic header carries a second button into `ADMIN_BASE_URL` —
`apps/users/admin.py` puts balance with the real/bonus split, subscriptions with
device counts, and the last ten transactions on one page. `telegram_id` is
read-only on an existing row and the transaction inline cannot rewrite or delete
an existing entry: balance is the sum of every row.

**Risks**

- One open ticket per user is held by a partial unique index, and the code has
  to be able to lose that race and pick up the other row. The operator claim
  uses the same trick — the `isnull` condition lives in the `UPDATE`.
- The support message filter is registered **before** the successful-payment
  handler. Widening it further without excluding service messages would swallow
  payment confirmations.
- An empty `ADMIN_BASE_URL` must drop the admin button rather than emit a broken
  URL: Bot API rejects an entire keyboard over one invalid link, which would
  take «Закрыть обращение» with it.

---

## Django admin and its static files

**Files** `apps/users/admin.py` (the customer page), `apps/vpn/admin.py`,
`apps/payments/admin.py`, the `STORAGES`/`MIDDLEWARE` block in `bot/settings.py`,
the `collectstatic` step in `Dockerfile`.

**How it behaves today** `DEBUG=False`, so Django serves no static itself and
nginx proxies `/static/` into the container rather than reading the files. The
application therefore serves its own assets: WhiteNoise sits directly behind
`SecurityMiddleware`, and `collectstatic` runs **at image build**, not only in
`entrypoint.sh` where it is wrapped in `|| true` and can fail silently. Storage
is `CompressedStaticFilesStorage`, deliberately not the manifest variant: a
missing manifest entry turns every admin page into a 500 instead of a page
without one icon.

**Risk** client UUIDs and subscription URLs are bearer data and must not appear
in any changelist. Admin lists are screenshotted and shared far more often than
detail pages.

---

## Mirror and backup ingestion

**Files** `apps/subscriptions/views.py`, from `_backup_links` to
`_build_mirror_vless`; the secret loader `_backup_secret_from_secret_file` in
`bot/settings.py`.

**How it behaves today** Provider documents are classified by transport before
rendering. JSON documents (sing-box, v2ray arrays) are parsed and only TLS or
Reality endpoints are emitted; plaintext VLESS is withheld behind
`SUBSCRIPTION_BACKUP_ALLOW_PLAINTEXT_ENDPOINTS` because plain VLESS puts the
client UUID on the wire. Opaque URI-list sources are preserved byte-for-byte and
are **not** gated — guessing transport security from an incomplete URI would
break the byte-for-byte contract tests. Providers pick their format by client
User-Agent, so `SUBSCRIPTION_BACKUP_UPSTREAM_USER_AGENT` is configuration.

Bearer URLs come only from a mode-0600 JSON file mounted into `web`; a missing,
malformed, symlinked or over-permissive file yields no endpoints and logs
nothing. See [`MIRROR-INBOUNDS-SPEC.md`](MIRROR-INBOUNDS-SPEC.md).

---

## Monitoring

**Files** `apps/monitoring/tasks.py` (`run_control_plane_monitor` L0,
`run_regional_monitor` L1, `run_protocol_monitor` L2,
`run_host_capacity_monitor`), `probes.py`, `notifications.py`, `models.py`.

**Flags** `SPECIAL_MONITOR_ENABLED` and `SPECIAL_MONITOR_L2_ENABLED` build the
beat schedule at import time in `bot/settings.py` — changing them requires a
restart of beat, not just a signal. Paging is a separate flag and is off.

**Rule** monitoring observes. It never restarts a service, fails over, or
mutates a customer. See [`MONITORING.md`](MONITORING.md).

---

## Deploying

See [`DEPLOY.md`](DEPLOY.md). The short version: push to
`special-router/special-bot` main, then run
`ops/scripts/deploy_special_subscription_app.sh`, which fetches, builds,
migrates through a one-off compose run, and force-recreates `web celery
celery_beat monitoring`. PostgreSQL and Redis are not declared in the deploy
compose file and are never touched.

---

## Responding to an incident

1. [`RUNBOOK.md`](RUNBOOK.md) — read-only checks first, then the mutation gates.
2. [`MONITORING.md`](MONITORING.md) — what each layer actually proves. A TCP
   connect is reachability, not protocol health.
3. [`INBOUND-DIAGNOSTICS-SPEC.md`](INBOUND-DIAGNOSTICS-SPEC.md) — the per-UUID
   symptom→layer table. **The automation in it is not implemented**; the tables
   are still the fastest way to classify a report.
4. [`TIMEWEB-BOT-SSH-RECOVERY.md`](TIMEWEB-BOT-SSH-RECOVERY.md) — when BOT SSH
   itself is the casualty.

Never restart a service as part of a diagnostic step.
