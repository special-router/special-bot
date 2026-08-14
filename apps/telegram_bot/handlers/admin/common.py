"""Shared plumbing for the in-bot admin panel: pending free-text input.

Every other flow in this bot is button-driven; nothing anywhere else reads a
plain text reply. The admin panel needs one for a client identifier, a
broadcast title/message and a credit amount, so a small piece of per-admin
state tracks which of those a reply answers. It lives in PTB's own
`context.user_data` (in-process only; the bot has no persistence configured)
rather than a model — only a handful of trusted operators use it, and losing
an in-progress prompt on a bot restart costs the admin one more tap.
"""
from __future__ import annotations

from telegram.ext import filters

from apps.telegram_bot.admin_auth import is_bot_admin


AWAITING_KEY = 'admin_awaiting'

AWAITING_CLIENT_LOOKUP = 'client_lookup'
AWAITING_BALANCE_AMOUNT = 'balance_amount'
AWAITING_BROADCAST_TITLE = 'broadcast_title'
AWAITING_BROADCAST_MESSAGE = 'broadcast_message'


class _AdminPendingInputFilter(filters.MessageFilter):
    """Match a private text message only from an admin who is mid-prompt.

    Checked against the `Application`'s own `user_data`, because a PTB filter
    sees only the message, not `context`. The import happens inside `filter()`:
    `bot_app` builds the `Application` at import time from
    `TELEGRAM_BOT_TOKEN`, and importing it at module load here would hit the
    same `InvalidToken` trap `register_handlers` already avoids.
    """

    def filter(self, message):
        from_user = message.from_user
        if from_user is None or not is_bot_admin(from_user.id):
            return False

        from apps.telegram_bot.bot_app import telegram_bot_app

        pending = telegram_bot_app.user_data.get(from_user.id, {})
        return bool(pending.get(AWAITING_KEY))


ADMIN_PENDING_INPUT = _AdminPendingInputFilter(name='AdminPendingInput')


def begin_awaiting(context, awaiting: str, **flow_state) -> None:
    """Mark this admin as mid-prompt for `awaiting`, storing any flow state."""
    context.user_data[AWAITING_KEY] = awaiting
    context.user_data.update(flow_state)


def stop_awaiting(context, *extra_keys: str) -> None:
    """Clear the pending prompt and any flow state named in `extra_keys`."""
    context.user_data.pop(AWAITING_KEY, None)
    for key in extra_keys:
        context.user_data.pop(key, None)
