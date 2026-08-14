"""Routes a pending admin's free-text reply to the flow waiting for it.

No `@admin_only` here: the `ADMIN_PENDING_INPUT` filter this handler is
registered against already re-checks `is_bot_admin` on every message, and only
matches when that admin has an active prompt — the filter *is* the guard.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from apps.telegram_bot.handlers.admin import broadcast, client, money
from apps.telegram_bot.handlers.admin.common import (
    AWAITING_BALANCE_AMOUNT,
    AWAITING_BROADCAST_MESSAGE,
    AWAITING_BROADCAST_TITLE,
    AWAITING_CLIENT_LOOKUP,
    AWAITING_KEY,
)


_ROUTES = {
    AWAITING_CLIENT_LOOKUP: client.handle_lookup_text,
    AWAITING_BALANCE_AMOUNT: money.handle_amount_text,
    AWAITING_BROADCAST_TITLE: broadcast.handle_title_text,
    AWAITING_BROADCAST_MESSAGE: broadcast.handle_message_text,
}


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    route = _ROUTES.get(context.user_data.get(AWAITING_KEY))
    if route is None:
        return
    await route(update, context)
