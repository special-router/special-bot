"""Who may open the bot's own admin panel.

The bot has no operator identity otherwise: unlike the support chat, where the
first human to reply inside a ticket topic claims it, `/admin` and every
`admin_*` callback answer only a fixed Telegram-id allowlist, checked fresh on
every entry — a stale or forwarded admin button must fail the same way a
forged one does.
"""
from __future__ import annotations

from functools import wraps

from django.conf import settings


def is_bot_admin(telegram_id: int) -> bool:
    """Whether this Telegram id may act on the in-bot admin panel.

    Empty or malformed `BOT_ADMIN_TELEGRAM_IDS` means nobody is admin, never
    everybody — the same fail-safe an empty rollout allowlist uses elsewhere in
    this codebase (`apps.subscriptions.views._is_backup_test_user`).
    """
    admin_ids = getattr(settings, 'BOT_ADMIN_TELEGRAM_IDS', [])
    return isinstance(admin_ids, list) and bool(admin_ids) and telegram_id in admin_ids


def admin_only(handler):
    """Silently drop the update unless its sender is a bot admin.

    Every admin command and callback goes through this, not only the menu
    entry point. A non-admin must get zero response — not an error, not a
    toast — so an admin surface cannot be distinguished from an unrecognized
    command, matching this codebase's `_refused()` reasoning for the
    subscription endpoint.
    """

    @wraps(handler)
    async def _wrapped(update, context):
        from_user = update.effective_user
        if from_user is None or not is_bot_admin(from_user.id):
            return
        await handler(update, context)

    return _wrapped
