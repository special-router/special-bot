#!/usr/bin/env python3
"""Walk the SPECIAL bot's inline UI as a real user and fail on silent buttons.

Unit tests call screen builders, so a button whose handler never answers its
callback, never edits anything, or dies inside Bot API still passes them.  This
harness presses the buttons instead: every reachable screen is reached from
``/start``, and each press must produce an answered callback and a changed
screen carrying a keyboard.  The `tg` CLI cannot do this — it has no way to
click an inline button — so Telethon's ``message.click()`` is used directly.

Running it
----------

Telethon is not in the project venv; use the pi-telethon one::

    ~/Projects/pi-telethon/.venv/bin/python ops/scripts/verify_bot_ui.py

Credentials and the session come from an existing pi-telethon profile home
(``TELETHON_HOME``, default ``~/.pi-telethon``).  Nothing about them is
printed: not the path, not the api hash, not the phone number.

  NOTE: the intended profile ``arbitron_adm`` currently holds no credentials.
  Log it in first (``/telegram-login arbitron_adm``); until then the harness
  exits 3 without contacting Telegram.

Flags
-----

  --profile NAME       pi-telethon profile to drive the walk as.
  --bot USERNAME       bot to walk.
  --allow-mutations    also press the buttons that spend money or destroy
                       data: buying and deleting a subscription, claiming the
                       promo grant, opening an invoice, unbinding devices and
                       filing a support ticket.  Without it those are reported
                       as SKIP and never pressed — the default run changes
                       nothing beyond ``/start`` and navigation.
  --timeout SECONDS    how long one press may take before it counts as
                       unanswered.
  --list-actions       print the safe/mutating classification and exit; needs
                       no credentials and no network.

Exits 0 when every visited screen passed, 1 on any failure, 2 on bad
arguments, 3 when the profile cannot be used.  Non-zero is meant to gate a
deploy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROFILE = 'arbitron_adm'
DEFAULT_BOT = 'SpecialVPNbot'
DEFAULT_TIMEOUT = 20.0

PROFILE_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')

# Действия, которые тратят деньги или уничтожают данные. Ничто из этого не
# нажимается без --allow-mutations, поэтому обычный прогон безопасен на живом
# аккаунте. Сверяется по префиксу: у add_key/remove_key в callback_data есть
# переменный хвост.
MUTATING_PREFIXES = (
    'add_key:',
    'remove_key:',
    'top_up_balance_',
    'reset_devices',
    'support_open',
)

# Текст глобального обработчика ошибок, apps/telegram_bot/error_handler.py.
# Экран с ним означает, что нажатие упало, а не отработало.
FAILURE_MARKERS = ('Не удалось выполнить действие',)

# Честные отказы: бот работает, но функция выключена настройкой. Это состояние
# отмечается в отчёте и не роняет прогон — иначе гейт был бы красным всё
# время, пока провайдер не подключён.
NOTICE_MARKERS = ('Пополнение временно недоступно',)

PASS = 'PASS'
FAIL = 'FAIL'
SKIP = 'SKIP'


class ProfileUnusable(RuntimeError):
    """The profile cannot drive a walk; the message never names a path."""


@dataclass(frozen=True)
class Outcome:
    screen: str
    status: str
    note: str


def normalize(data: str) -> str:
    """Свернуть переменный хвост, чтобы одноразовый nonce не ломал обход."""
    return re.sub(r':\d+$', ':*', data)


def is_mutating(data: str) -> bool:
    return any(data.startswith(prefix) for prefix in MUTATING_PREFIXES)


def screen_name(path: tuple[str, ...]) -> str:
    return ' > '.join(('/start', *(normalize(step) for step in path)))


def load_credentials(profile: str) -> tuple[str, int, str]:
    """Return (session, api_id, api_hash) for an already logged-in profile.

    Raises `ProfileUnusable` with a message that names only the profile: the
    session path and the api hash must not reach the report or the logs.
    """
    if not PROFILE_RE.fullmatch(profile):
        raise ProfileUnusable('profile name is not a valid identifier')

    home = Path(os.environ.get('TELETHON_HOME') or Path.home() / '.pi-telethon').expanduser()
    session = home / 'sessions' / profile
    credentials = session.with_suffix('.creds.json')

    if not credentials.exists():
        raise ProfileUnusable(f'profile {profile!r} has no stored credentials; log it in first')

    try:
        stored = json.loads(credentials.read_text(encoding='utf-8'))
        return str(session), int(stored['api_id']), str(stored['api_hash'])
    except (OSError, ValueError, KeyError) as error:
        raise ProfileUnusable(f'profile {profile!r} has unreadable credentials: {type(error).__name__}') from None


def callbacks_of(message) -> list[str]:
    """`callback_data` кнопок экрана. Кнопки-ссылки не нажимаются."""
    markup = getattr(message, 'reply_markup', None)
    if markup is None:
        return []

    found: list[str] = []
    for row in getattr(markup, 'rows', ()):
        for key in getattr(row, 'buttons', ()):
            data = getattr(key, 'data', None)
            if isinstance(data, bytes):
                found.append(data.decode('utf-8', 'replace'))
    return found


async def _newest_incoming(client, bot):
    messages = await client.get_messages(bot, limit=1)
    for message in messages:
        if not message.out:
            return message
    return None


async def _wait_for_reply(client, bot, after_id: int, timeout: float):
    """Дождаться сообщения бота новее `after_id`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        newest = await _newest_incoming(client, bot)
        if newest is not None and newest.id > after_id:
            return newest
        await asyncio.sleep(0.4)
    return None


async def _wait_for_screen_change(client, bot, anchor, timeout: float):
    """Экран после нажатия: правка того же сообщения либо новое сообщение.

    Бот перерисовывает якорь на месте, поэтому «ничего не появилось» — не
    признак отказа; сравнивается текст. Неизменившийся экран как раз и есть
    та пропавшая обратная связь, ради которой написан этот обход.
    """
    before = anchor.text or ''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        newest = await _newest_incoming(client, bot)
        if newest is not None and newest.id > anchor.id:
            return newest

        refreshed = await client.get_messages(bot, ids=anchor.id)
        if refreshed is not None and (refreshed.text or '') != before:
            return refreshed

        await asyncio.sleep(0.4)
    return None


async def press(client, bot, anchor, data: str, timeout: float):
    """Нажать кнопку и вернуть (экран, замечание). Экран `None` — провал."""
    from telethon import errors

    # Экран пересобирается на каждом заходе, и одноразовый nonce в `add_key`
    # с ним меняется: нажимать надо то, что на клавиатуре сейчас, а не то,
    # что было записано в путь.
    target = next((option for option in callbacks_of(anchor) if normalize(option) == normalize(data)), None)
    if target is None:
        return None, f'кнопки {normalize(data)} на экране больше нет'

    try:
        answer = await anchor.click(data=target.encode('utf-8'))
    except errors.BotResponseTimeoutError:
        return None, 'нажатие осталось без ответа: «часики» не сняты'
    except errors.RPCError as error:
        return None, f'Bot API отклонил нажатие: {type(error).__name__}'

    toast = (getattr(answer, 'message', None) or '').strip() if answer is not None else ''

    screen = await _wait_for_screen_change(client, bot, anchor, timeout)
    if screen is None:
        note = 'экран не изменился'
        return None, f'{note}; тост: {toast}' if toast else note

    return screen, f'тост: {toast}' if toast else ''


def check_screen(screen) -> str:
    """Замечание к полученному экрану, пустая строка — всё в порядке."""
    text = screen.text or ''
    if not text.strip():
        return 'экран пришёл пустым'
    for marker in FAILURE_MARKERS:
        if marker in text:
            return f'на экране сообщение об отказе: {marker!r}'
    if getattr(screen, 'reply_markup', None) is None:
        return 'экран без клавиатуры — из него некуда идти'
    return ''


def notices_of(screen) -> str:
    """Выключенные настройкой функции: в отчёт, но не в провал."""
    text = screen.text or ''
    return ', '.join(marker for marker in NOTICE_MARKERS if marker in text)


async def visit(client, bot, path: tuple[str, ...], timeout: float):
    """Пройти путь от `/start` заново и вернуть (замечание, конечный экран)."""
    last_id = 0
    newest = await _newest_incoming(client, bot)
    if newest is not None:
        last_id = newest.id

    await client.send_message(bot, '/start')
    anchor = await _wait_for_reply(client, bot, last_id, timeout)
    if anchor is None:
        return '/start остался без ответа', None

    problem = check_screen(anchor)
    if problem:
        return problem, None

    note = ''
    for step in path:
        anchor, note = await press(client, bot, anchor, step, timeout)
        if anchor is None:
            return note, None
        problem = check_screen(anchor)
        if problem:
            return problem, None

    notice = notices_of(anchor)
    return '; '.join(part for part in (note, notice) if part), anchor


async def walk(client, bot, allow_mutations: bool, timeout: float) -> list[Outcome]:
    """Обойти все достижимые экраны в ширину, каждый раз от `/start`."""
    results: list[Outcome] = []
    queue: list[tuple[str, ...]] = [()]
    seen = {()}

    while queue:
        path = queue.pop(0)
        note, screen = await visit(client, bot, path, timeout)

        if screen is None:
            results.append(Outcome(screen_name(path), FAIL, note))
            continue

        results.append(Outcome(screen_name(path), PASS, note))

        for data in callbacks_of(screen):
            step = tuple(normalize(part) for part in (*path, data))
            if step in seen:
                continue
            seen.add(step)

            if is_mutating(data) and not allow_mutations:
                results.append(Outcome(screen_name((*path, data)), SKIP, 'изменяющее действие'))
                continue

            queue.append((*path, data))

    return results


def report(results: list[Outcome]) -> int:
    width = max((len(item.screen) for item in results), default=10)
    for item in results:
        line = f'{item.status:<4} {item.screen:<{width}}'
        print(f'{line}  {item.note}'.rstrip())

    failed = sum(1 for item in results if item.status == FAIL)
    skipped = sum(1 for item in results if item.status == SKIP)
    passed = len(results) - failed - skipped
    print(f'\nscreens_passed={passed} screens_failed={failed} screens_skipped={skipped}')
    return 1 if failed else 0


async def run(args: argparse.Namespace) -> int:
    from telethon import TelegramClient

    session, api_id, api_hash = load_credentials(args.profile)

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ProfileUnusable(f'profile {args.profile!r} is not logged in')

        results = await walk(client, args.bot, args.allow_mutations, args.timeout)
    finally:
        await client.disconnect()

    return report(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='verify_bot_ui.py',
        description='Press every reachable button of the SPECIAL bot and fail on silent ones.',
    )
    parser.add_argument('--profile', default=DEFAULT_PROFILE, help='pi-telethon profile to drive the walk as')
    parser.add_argument('--bot', default=DEFAULT_BOT, help='bot username to walk')
    parser.add_argument(
        '--allow-mutations',
        action='store_true',
        help='also press actions that spend money or destroy data (default: reported as SKIP)',
    )
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='seconds one press may take')
    parser.add_argument(
        '--list-actions',
        action='store_true',
        help='print which callbacks count as mutating and exit; needs no credentials',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.timeout <= 0:
        print('ERROR: --timeout must be positive', file=sys.stderr)
        return 2

    if args.list_actions:
        for prefix in MUTATING_PREFIXES:
            print(f'mutating {prefix}')
        print('everything else is walked by default')
        return 0

    try:
        return asyncio.run(run(args))
    except ProfileUnusable as error:
        print(f'BLOCK: {error}', file=sys.stderr)
        return 3
    except ImportError:
        print('BLOCK: telethon is missing; run this with the pi-telethon interpreter', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
