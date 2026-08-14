"""`/admin` entry point and the admin panel's main menu screen.

`/admin` is deliberately absent from `apps.telegram_bot.bot_app.post_init_handler`'s
public command list — it still works when typed, it just is not suggested to
every user.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.telegram_bot.admin_auth import admin_only
from apps.telegram_bot.handlers.admin.common import stop_awaiting
from apps.telegram_bot.ui import button, render_screen, screen


def _admin_menu_keyboard() -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [button('Клиент', 'admin_client')],
        [button('Мониторинг', 'admin_monitor')],
        [button('Рассылка', 'admin_broadcast')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_menu_screen() -> tuple[str, InlineKeyboardMarkup]:
    return screen('Админ-панель'), _admin_menu_keyboard()


@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stop_awaiting(context)
    text, keyboard = admin_menu_screen()
    await render_screen(update, context, text, keyboard, force_new=True)


@admin_only
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stop_awaiting(context)
    text, keyboard = admin_menu_screen()
    await render_screen(update, context, text, keyboard)
