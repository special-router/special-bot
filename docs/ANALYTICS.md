# Money flow analytics

The bot records every movement of balance as one signed `Transaction` row. That
table stays the source of truth for balance and this feature does not touch it:
`annotate_balance()` and the daily charge behave exactly as before. Everything
here is an additive analytical projection on top.

## The finding this exists to fix

Measured on production for 2025-09-17 → 2026-08-12:

| source | rows | sum |
|---|---|---|
| EVERYDAY_SYSTEM | 21721 | -152047.00 |
| BUY | 801 | -5607.00 |
| YOUMONEY | 353 | +126534.00 |
| MANUAL | 320 | +191386.00 |
| PROMO | 251 | +12299.00 |
| COMPENSATION | 44 | +2891.00 |
| REFERRAL | 17 | +2424.00 |

**Balance credited by hand (191k) exceeds money taken through the payment
provider (126k).** Any figure derived from balance is therefore not revenue.
The taxonomy below exists so that no report can accidentally add those two
numbers together.

## Taxonomy

`apps/analytics/taxonomy.py` is the single place that assigns economic meaning.
Reports read it; they never re-decide. A source it does not know is classified
`UNKNOWN` and printed on its own line, so a new payment method shows up as a gap
rather than quietly joining revenue.

The class depends on the source **and the sign**: `MANUAL` positive is a grant,
`MANUAL` negative is a correction, and a positive `EVERYDAY_SYSTEM` can only be
a reversal.

| source | sign | class | kind | cash basis |
|---|---|---|---|---|
| `YOUMONEY` | + | cash in | `TOPUP` | measured, or derived from the bonus ladder |
| `PROMO` | + | credit granted | `SIGNUP_PROMO` | none |
| `COMPENSATION` | + | credit granted | `OUTAGE_COMPENSATION` | none |
| `MANUAL` | + | credit granted | `MANUAL_CREDIT` | **unknown** |
| `MANUAL` | − | adjustment | `MANUAL_ADJUSTMENT` | none |
| `REFERRAL` | + | payout | `REFERRAL_PAYOUT` | none |
| `EVERYDAY_SYSTEM` | − | revenue | `DAILY_CHARGE` | none |
| `BUY` | − | revenue | `SUBSCRIPTION_PURCHASE` | none |
| any | unexpected sign | adjustment | `REVERSAL` | none |
| unknown source | any | unknown | `UNCLASSIFIED` | unknown |

Each row is split into four non-overlapping amounts — `cash_amount`,
`revenue_amount`, `credit_amount`, `payout_amount` — plus `balance_delta`, which
always equals the transaction amount and exists only for reconciling against
balance.

### A top-up is not one thing

`successful_payment_callback` adds a volume bonus before crediting the balance:
+5% above 400 ₽, +10% above 600, +20% above 1250, +30% above 2520. So a
`YOUMONEY` row of `3321` is 2555 ₽ of real money plus 766 ₽ of discount. The
split is what makes "cash received" and "what promotions cost" different
numbers.

Historical rows do not record the payment amount, so it is inverted from the
credited amount. The inversion is exact enough to trust: the credited ranges of
adjacent tiers do not overlap (`(0;400]`, `[420;630]`, `[660;1375]`,
`[1500;3024]`, `[3276;∞)`), so the tier is unambiguous, and the truncation in the
original formula bounds the error below 1 ₽ per payment. A credited amount that
falls in a gap between those ranges cannot have come from this ladder; it is
marked `UNKNOWN` and counted separately as `rows_outside_bonus_ladder`.

### The bucket that is honestly empty

`MANUAL` positive means the owner typed a balance in. Nothing in the schema says
whether money changed hands off-platform. It is counted as credit granted and
its cash basis is `UNKNOWN`, and the report prints it on a line of its own next
to real cash. **It is not added to cash in.** With 191k in this bucket, guessing
either way would be the single largest error the model could make.

## Реальные деньги и бонусы

`apps/analytics/balance_split.py` answers the one question the ledger could not:
of the balance standing on an account right now, how much did the customer pay
for and how much was given away. It is a **derived view, not a second store** —
both pots are recomputed from `Transaction` on every call, so they cannot drift
from the balance. There is no migration and no new money column.

`split_balance(user_id, as_of=None)` returns `BalanceSplit(real, bonus,
unclassified)`; `split_balances`, `attach_balance_split` and `aggregate_split`
are the bulk forms. `as_of` accepts a datetime or a date (meaning end of that
day). A true SQL annotation is impossible here: allocation is sequential and the
meaning of a row comes from the taxonomy in Python. Reimplementing that in a
window function would recreate exactly the second source of truth this avoids.

**The invariant.** `real + bonus == annotate_balance()` for every account at
every moment. It holds by construction — each ledger row moves the pair by
exactly its own amount — and is asserted per account in
`apps/analytics/test_balance_split.py`, plus reported live as
`mismatched_accounts` in `money_report`. Billing, entitlement and the `expire`
header keep reading the single balance and are untouched.

**The allocation rule, stated once.** A charge consumes **bonus first**, real
money only after the bonus is exhausted. A gift that outlives the money paid for
it would be a bonus the customer never reaches, so the screen would promise
something that never gets spent. Consequences:

- An account that was never granted anything has `bonus == 0` forever and
  `real == balance` — byte-identical behaviour to before this existed.
- Grants are held as one fungible number, not as dated lots. With no expiry on a
  grant, "oldest grant first" and any other order inside the bonus produce the
  same two numbers. Add expiry and lots belong here.
- A negative balance sits entirely on the real pot, and bonus is never negative.
  The sum invariant is non-negotiable, so for the 37 overdrawn accounts the
  deficit has to land somewhere; treating a debt as real money owed is the only
  reading that keeps «бонус» a spendable number.
- An inflow into an overdrawn account clears the debt before it shows up as
  bonus. Otherwise the screen would offer bonus to an account whose total is
  below zero.
- A reversal (a positive row where the source normally goes negative) returns to
  the bonus pot up to what earlier charges took from it, then to real — the
  mirror image of the consumption rule.
- A source the taxonomy does not know credits the **real** pot and is counted in
  `unclassified`. Calling an unknown inflow a gift would tell a paying customer
  their money was a present; the reverse error is invisible to them.

**A ladder top-up lands in both pots**, split by `taxonomy.split_topup` — the
same function `money_report` uses, so `real_total` in the split section and
`received_total` in the cash section are the same money by construction.

**What is still not knowable.** `MANUAL` positive — 191 386 ₽ — goes to bonus in
full, because nothing records whether cash changed hands off-platform. Some of
that pot is very probably real money, and no query can say whose. The split makes
the ambiguity a visible number instead of an assumption; it does not resolve it.

In the bot, the combined total is unchanged on every screen. The breakdown line
appears under the total on the balance and profile screens, and only when the
bonus is above zero; `BALANCE_SPLIT_UI_ENABLED=false` removes it. Django admin
shows all three numbers for one account.

## Event log

Two append-only tables in `apps/analytics/models.py`:

- `MoneyEvent` — one row per `Transaction`, `event_key = tx:<id>`, unique.
- `FunnelEvent` — one row per customer-journey step, `event_key` chosen by the
  call site.

The unique `event_key` is what makes a retry, a re-run of the backfill and a
double button press all land on the same row. It is also why the backfill can
safely top up a log the live path has already written.

`effective_date` is the day a report attributes the row to: `charge_date` when
the daily charge set it, otherwise the creation day. `date_basis` records which,
because rows older than migration `0006` have no `charge_date`.

Nothing secret goes in: no subscription URLs, client UUIDs, payment charge ids or
message text. The payment charge id is hashed into an idempotency key and not
stored.

### Why events are written after commit

Recording is scheduled with `transaction.on_commit` and every exception is
swallowed into the log (`apps/analytics/recording.py`). Writing the event inside
the money transaction would be more consistent, at the price of letting a failed
analytics insert roll back a charge, a payment or a compensation grant.

The trade is asymmetric. A lost event is recoverable — `Transaction` is still the
source of truth and `backfill_money_events` rewrites it under the same key.
A charge that did not happen is not recoverable. In autocommit — where the bot
handlers run — `on_commit` fires immediately, so nothing is delayed.

Capture happens through one `post_save` receiver on `Transaction`
(`apps/analytics/signals.py`), so the six places that create money rows do not
have to know analytics exists and cannot forget to call it.

`ANALYTICS_EVENTS_ENABLED=false` removes even the extra insert.

## Backfill

```bash
python manage.py backfill_money_events [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--refresh]
```

Idempotent and re-runnable. `--refresh` re-classifies existing events after a
change to the taxonomy. It also derives account-level churn from the series of
daily-charge dates.

**What history cannot reconstruct.** Stated plainly, because a model built on a
guess is worse than no model:

- Daily charges older than migration `0006` have no `user_vpn` and no
  `charge_date`. Churn is therefore per **account** (`ACCOUNT_BILLING_LAPSED` /
  `ACCOUNT_BILLING_RESUMED`), not per subscription.
- A lapse that never resumes is indistinguishable from a subscription the user
  deleted. Deletions were never recorded.
- Whether a `MANUAL` credit was a gift or off-platform cash.
- Whether the top-up bonus ladder had these thresholds throughout the period. If
  it changed, older top-ups are split by today's rules.
- Funnel steps before payment. Nothing recorded them, and no amount of history
  invents them — the numbers start from the day the call sites are wired.

## Report

```bash
python manage.py money_report --since 2026-07-01 --until 2026-07-31 [--cohorts 12] [--json]
```

Sections: cash in, recognised revenue and how it was funded, credit granted by
promotion, payouts, adjustments, customers and ARPU, promo conversion, referral
programme margin, churn, funnel, the standing balance split, and signup cohorts.
`--json` prints the same data machine-readably.

`BALANCE SPLIT` is the only section that is a *stock* rather than a flow: it is
the balance standing at the period end, split into money and gifts by the rule
above, and it is computed from `Transaction` rather than from the event log.
`mismatched_accounts` must read `0` — anything else means the split and the
balance have parted company on live data.

Two lines are estimates and are labelled as such in the output:

- `funded_by_cash` splits the period's revenue by each account's lifetime mix of
  paid and free inflow. A charge does not record which rouble it consumed, so
  this is a proportional attribution, not a fact.
- `provider_topup` uses the derived payment amounts described above.

Promo conversion, cohorts and the referral `*_lifetime` lines are computed over
all history up to the period end, not inside the period: "did this discount pay
for itself" is not a question a single month can answer.

## Funnel API

`apps/analytics/funnel.py` holds one function per step and the exact call site
for each in its module docstring. **None of them are wired**, except
`subscription_disabled_no_funds`, which belongs to billing rather than to a
button and is called from `apps/subscriptions/tasks.py`. Wiring is one line per
site and none of them can raise.

## Validation

```bash
env DJANGO_SETTINGS_MODULE=bot.settings DATABASE_URL='sqlite:///:memory:' \
  CELERY_ALWAYS_EAGER=true <venv>/python -m pytest apps/analytics -q
```
