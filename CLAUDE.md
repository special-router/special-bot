# SPECIAL Bot — start here

Django + python-telegram-bot + Celery. It sells VPN access, bills a shared
per-account balance daily, and renders one per-user subscription document that
VPN clients import. The transport itself is 3x-ui/xray on a separate host; this
repository never owns the tunnel, only who is entitled to it.

Read this file, then [`docs/CONTEXT-MAP.md`](docs/CONTEXT-MAP.md) for the task
you were actually given. Together they are two minutes and replace an hour of
searching.

## Three hosts, routinely confused

| Name here | Address | What runs there | Access |
|---|---|---|---|
| **BOT** | `72.56.23.226` | Django (`web`), celery, celery_beat, monitoring, broadcast; PostgreSQL 16 and Redis as separate shared containers | SSH `specialops` + `sudo -n` |
| **NL** | `sub.special-wifi.ru` | the 3x-ui panel, xray and every inbound, nginx terminating TLS and SNI-routing `:443` | SSH `specialops`; **`root` is refused** |
| **RU relay** | `Server.client_vpn_host` in the DB | byte-transparent nginx stream relay in front of NL | separate legacy credential, see [`docs/RUNBOOK.md`](docs/RUNBOOK.md) |

The NL host is the `Server` row named «Нидерланды». The relay is **not** the NL
host: it is whatever `client_vpn_host` holds, and it is the only source of the
`🇳🇱 NL Relay` line in a subscription.

## The one validation command

```bash
SPECIAL_VERIFY_PYTHON=<absolute path to the project test venv python> \
  ./ops/scripts/verify_scale_closeout.sh
```

It runs the repository validators, `makemigrations --check`, and the full pytest
suite. **442 tests and 226 subtests pass as of 2026-08-13** — compare against that
number, because a new test file whose name does not match `test_*.py` is
silently never collected and leaves the count unchanged. `SPECIAL_VERIFY_PRODUCTION`
defaults to false, so nothing touches a server. Ask the operator for the venv
path; it is deliberately not written down here.

To iterate on one file, export the same environment the script does:

```bash
env DJANGO_SETTINGS_MODULE=bot.settings DATABASE_URL='sqlite:///:memory:' \
  CELERY_ALWAYS_EAGER=true <venv>/python -m pytest apps/subscriptions/test_billing.py -q
```

Tests run on SQLite; production is PostgreSQL 16. That gap is real and has
caught a failure that the local run could not — see
[`docs/DEPLOY.md`](docs/DEPLOY.md#run-the-suite-against-postgresql-before-declaring-a-deploy-safe).

## Rules that exist because breaking them cost an incident

- **Check for live traffic and ownership before blocking a port, disabling an
  inbound, or removing a client.** Several externally reachable ports on NL have
  users nobody remembers adding.
- **Money goes through `annotate_balance()`.** It sums *all* transactions
  regardless of status. Writing a filtered `Sum` makes the charged amount
  disagree with the balance the user is shown.
- **PostgreSQL and Redis are never restarted by a deploy.** They are not even
  declared in `docker-compose.deploy.yml`.
- **The 3x-ui panel base path is a bearer secret.** `httpx`/`httpcore`/`py3xui`
  loggers are pinned to `WARNING` in `bot/settings.py` because they log full
  request URLs at `INFO`. Never lower them.
- **A subscription URL is bearer access data.** It never enters logs, docs,
  tickets, dashboards, or monitoring state. Neither do client UUIDs.
- **`_refused()` is the only 404 the subscription endpoint produces.** Unknown
  `sub_id`, disabled subscription and refused device must stay indistinguishable.

## Where everything is

| Question | Document |
|---|---|
| What files/hosts/flags/risks does *my kind of task* touch? | [`docs/CONTEXT-MAP.md`](docs/CONTEXT-MAP.md) |
| What is every setting, its default and its production value? | [`docs/FLAGS.md`](docs/FLAGS.md) |
| What is unfinished, and what exactly blocks it? | [`docs/OPEN-ITEMS.md`](docs/OPEN-ITEMS.md) |
| What does the money actually do, and what can the data not tell me? | [`docs/ANALYTICS.md`](docs/ANALYTICS.md) |
| How does traffic actually flow? | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| What is live in production right now? | [`docs/STATUS.md`](docs/STATUS.md) |
| How do I ship it? | [`docs/DEPLOY.md`](docs/DEPLOY.md) |
| Something is broken in production | [`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`docs/MONITORING.md`](docs/MONITORING.md) |
| A user cannot connect | [`docs/INBOUND-DIAGNOSTICS-SPEC.md`](docs/INBOUND-DIAGNOSTICS-SPEC.md) (design only, not built) |

## What this repository cannot tell you

These are facts about running systems. The repository has no way to check them,
so every one of them is dated and operator-reported. Verify before acting.

- The contents of `.environment` on BOT, which is what actually sets every flag.
  `bot/settings.py` gives you the *default*, not the running value.
- Tariff prices, server rows, inbound ids and `client_vpn_host` — all database
  rows. `7.00 ₽/day` is data, not a constant in the code.
- Which ports are open on NL, and who is using them.
- Whether the operators' Telegram supergroup exists, and whether the bot owner
  holds Telegram Premium. Two shipped features are inert without them.
