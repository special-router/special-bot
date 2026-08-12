"""Обращения в поддержку внутри бота: клиент здесь, операторы — в темах чата.

Разговор клиента и оператора идёт через две разные точки входа: сообщение из
личного чата уходит в тему, сообщение из темы возвращается клиенту. Общего у
них только тикет, и весь порядок работы с ним — в `apps.telegram_bot.support`.

Вложение всегда уходит вторым сообщением, а текст — первым. Порядок не
косметический: подпись к файлу и сам файл ходят по разным вызовам Bot API, и
отказ на файле не должен утаскивать с собой то, что человек написал. Поэтому
подпись пересылается как текст, а не как `caption`, и потеря вложения остаётся
потерей одного вложения, о которой отправителю сообщают.
"""

from __future__ import annotations

import html
from typing import Final
from urllib.parse import urljoin

from asgiref.sync import sync_to_async
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, filters

from apps.telegram_bot import icons
from apps.telegram_bot.inline_buttons.back import get_reply_markup_back
from apps.telegram_bot.models import SupportTicket
from apps.telegram_bot.support import (
    attach_topic,
    claim_ticket,
    close_ticket,
    consume_prompt,
    discard_draft,
    open_prompt,
    open_ticket,
    remember_topic_message,
    ticket_by_topic,
    truncate,
)
from apps.telegram_bot.ui import (
    NO_PREVIEW,
    PARSE_MODE,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    answer_query,
    back_button,
    button,
    render_screen,
    screen,
)
from apps.telegram_bot.utils import get_user
from apps.users.models import TelegramUser


INVITATION_BODY: Final[str] = (
    'Опишите проблему одним сообщением — оно уйдёт оператору, ответ придёт сюда же. '
    'Чтобы дополнить обращение, нажмите «Поддержка» ещё раз.'
)

UNAVAILABLE_BODY: Final[str] = (
    'Не удалось создать обращение. Попробуйте ещё раз через несколько минут — '
    'если не получится, напишите нам в чат поддержки.'
)

UNSUPPORTED_BODY: Final[str] = (
    'Такое вложение оператор не получит. Приложите фотографию, видео, файл, '
    'голосовое или видеосообщение — либо опишите проблему текстом. '
    'Чтобы отправить заново, нажмите «Поддержка» ещё раз.'
)

ATTACHMENT_LOST_BODY: Final[str] = (
    'Вложение до оператора не дошло — всё остальное он получил. '
    'Чтобы отправить файл ещё раз, нажмите «Поддержка» и приложите его снова.'
)

# Bot API обрезает имя темы на 128 символах; обрезаем сами, чтобы не гадать,
# что именно уцелело из имени пользователя.
TOPIC_NAME_MAX_LENGTH: Final[int] = 128

# Подпись клиенту, когда у оператора не осталось ни имени, ни `@username`:
# числовой id — не подпись, а внутренний идентификатор, и клиенту он не нужен.
ANONYMOUS_OPERATOR: Final[str] = 'Оператор'

# Что пересылается в обе стороны. Ключ — имя поля в `Message`, и оно же имя
# аргумента у соответствующего `send_*`, поэтому таблица соответствий не нужна.
MEDIA_KINDS: Final[dict[str, str]] = {
    'photo': 'фотография',
    'video': 'видео',
    'document': 'файл',
    'voice': 'голосовое сообщение',
    'video_note': 'видеосообщение',
}

MEDIA_FILTER: Final = filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.VOICE | filters.VIDEO_NOTE

# Типы, которые обработчик обязан именно отклонить, а не пропустить мимо себя.
# Без них стикер или кружок геолокации не дошёл бы ни до какого обработчика, и
# отправитель не узнал бы, что его сообщение никуда не ушло.
REFUSED_MEDIA_FILTER: Final = (
    filters.ANIMATION
    | filters.AUDIO
    | filters.Sticker.ALL
    | filters.CONTACT
    | filters.LOCATION
    | filters.VENUE
    | filters.POLL
)

SUPPORT_MESSAGE_FILTER: Final = (filters.TEXT | MEDIA_FILTER | REFUSED_MEDIA_FILTER) & ~filters.COMMAND


def _enabled() -> bool:
    """Вторая застава помимо регистрации обработчиков.

    Регистрация читает настройку один раз при старте; обработчик — на каждом
    вызове. Обе проверки дублируют друг друга намеренно: включение функции
    зависит от чата, которого может не быть, и цена ошибки здесь — тикет,
    который некуда положить.
    """
    return bool(settings.SUPPORT_CHAT_ID)


def _mention(telegram_user) -> str:
    """Как обращение подписано для оператора.

    У пользователя может не быть `@username` вовсе, а числовой id ищется в
    админке — поэтому запасной вариант тоже пригоден для работы, а не заглушка.
    """
    if telegram_user is not None and telegram_user.username:
        return f'@{telegram_user.username}'
    return f'id{telegram_user.id}' if telegram_user is not None else 'id0'


def _operator_name(telegram_user) -> str:
    """Как ответ подписан для клиента.

    Имя, потом `@username`, потом безличная подпись. Числового id здесь нет и не
    будет: он ничего не говорит клиенту и раскрывает аккаунт оператора там, где
    тот ничего публиковать не соглашался. По той же причине подпись — текст, а
    не ссылка на профиль.
    """
    if telegram_user is None:
        return ANONYMOUS_OPERATOR

    parts = (getattr(telegram_user, 'first_name', '') or '', getattr(telegram_user, 'last_name', '') or '')
    full_name = ' '.join(part for part in parts if part).strip()
    if full_name:
        return truncate(full_name, SupportTicket.OPERATOR_NAME_MAX_LENGTH)

    username = getattr(telegram_user, 'username', '') or ''
    if username:
        return truncate(f'@{username}', SupportTicket.OPERATOR_NAME_MAX_LENGTH)

    return ANONYMOUS_OPERATOR


def _topic_name(ticket_id: int, mention: str, *, operator: str = '', closed: bool = False) -> str:
    marker = STATUS_INACTIVE if closed else STATUS_ACTIVE
    handler = f' · {operator}' if operator else ''
    return truncate(f'{marker} Ticket #{ticket_id} | {mention}{handler}', TOPIC_NAME_MAX_LENGTH)


def _admin_user_url(user_pk: int) -> str | None:
    """Ссылка на карточку клиента в админке, или ``None``, если её некуда вести.

    Путь берётся из `reverse()`, а из настройки — только схема и хост: так
    ссылка не рассыплется, если админку перевесят на другой префикс. Пустая
    настройка обязана давать ``None``, а не битый адрес: Bot API отклоняет
    клавиатуру целиком из-за одной невалидной кнопки, и вместе с ней пропадёт
    кнопка закрытия обращения.
    """
    base = (getattr(settings, 'ADMIN_BASE_URL', '') or '').strip()
    if not base:
        return None

    try:
        path = reverse('admin:users_telegramuser_change', args=[user_pk])
    except NoReverseMatch:
        return None

    return urljoin(base, path)


async def build_support_screen() -> tuple[str, InlineKeyboardMarkup]:
    """Приглашение написать. Экран собирается без базы — как остальные."""
    text = screen(
        'Поддержка',
        state=['Следующее ваше сообщение уйдёт оператору.'],
        body=[INVITATION_BODY],
    )
    return text, await get_reply_markup_back()


async def build_sent_screen(ticket_id: int, *, attachment_lost: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    body = ['Оператор ответит в этом чате. Чтобы что-то добавить, нажмите «Поддержка» ещё раз.']
    if attachment_lost:
        # Первой строкой, до общих слов: молча отправленное «обращение принято»
        # после потерянного файла — это и есть тихая потеря сообщения.
        body.insert(0, ATTACHMENT_LOST_BODY)

    text = screen('Обращение отправлено', state=[f'Обращение № {ticket_id}'], body=body)
    return text, await get_reply_markup_back()


async def build_unavailable_screen() -> tuple[str, InlineKeyboardMarkup]:
    return screen('Поддержка', body=[UNAVAILABLE_BODY]), await get_reply_markup_back()


async def build_unsupported_screen() -> tuple[str, InlineKeyboardMarkup]:
    return screen('Вложение не отправлено', body=[UNSUPPORTED_BODY]), await get_reply_markup_back()


def _reply_keyboard() -> InlineKeyboardMarkup:
    """Ответ оператора — тупик без кнопок, поэтому продолжение предлагается сразу."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[button('Поддержка', 'support_open', icon=icons.PEOPLE), back_button()]]
    )


def _topic_keyboard(ticket: SupportTicket) -> InlineKeyboardMarkup:
    """Кнопки под обращением в теме: закрыть и открыть карточку клиента.

    Карточка нужна ровно там, где оператор решает: баланс, промо, тариф и
    подписки лежат на одной странице админки, и без кнопки её пришлось бы
    искать поиском по telegram_id.
    """
    row = [button('Закрыть обращение', f'support_close:{ticket.id}')]

    admin_url = _admin_user_url(ticket.user_id)
    if admin_url is not None:
        row.append(button('Карточка клиента', url=admin_url))

    return InlineKeyboardMarkup(inline_keyboard=[row])


def _extract_media(message) -> tuple[str, str] | None:
    """Вид вложения и его ``file_id``, или ``None``, если пересылать нечего.

    Содержимое файла не читается и никуда не сохраняется: Bot API умеет
    пересылать по идентификатору, и это единственное, что попадает в код.
    """
    for kind in MEDIA_KINDS:
        attachment = getattr(message, kind, None)
        if not attachment:
            continue
        if kind == 'photo':
            # `photo` — лестница размеров одного снимка, последний самый крупный.
            attachment = attachment[-1]
        return kind, attachment.file_id

    return None


async def _relay_media(
    context: ContextTypes.DEFAULT_TYPE,
    media: tuple[str, str],
    *,
    chat_id: int,
    message_thread_id: int | None = None,
) -> bool:
    """Переслать вложение по ``file_id``. ``False`` — Telegram отказал.

    Отказ здесь ожидаем: файл мог быть удалён, а видеосообщение — превысить
    предел, о котором бот узнаёт только от Bot API. Исключение наверх не идёт,
    потому что текст уже доставлен и обработчику остаётся сказать об этом
    отправителю, а не упасть.
    """
    kind, file_id = media
    try:
        await getattr(context.bot, f'send_{kind}')(
            chat_id=chat_id, message_thread_id=message_thread_id, **{kind: file_id}
        )
    except TelegramError:
        return False

    return True


async def support_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Поддержка»: включить ожидание и позвать написать."""
    if not _enabled():
        # Обработчик без чата операторов не регистрируется, так что сюда
        # попадает только нажатие, пережившее выключение настройки.
        await answer_query(update, 'Обращения временно недоступны.')
        return

    user: TelegramUser = await get_user(update)
    await sync_to_async(open_prompt)(user.id)

    text, keyboard = await build_support_screen()
    await render_screen(update, context, text, keyboard)


async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сообщение из личного чата: уходит в тикет, если его ждали.

    Ожидание снимается первым делом. Если снять его позже, любой сбой на
    стороне Telegram оставил бы пользователя в режиме, где каждое следующее
    слово улетает оператору.
    """
    if not _enabled():
        return

    user: TelegramUser = await get_user(update)
    if not await sync_to_async(consume_prompt)(user.id):
        return

    message = update.message
    message_text = truncate(message.text or message.caption)
    media = _extract_media(message)

    if media is None and not message_text:
        # Сюда попадает вложение, которое бот пересылать не умеет. Ожидание уже
        # снято, поэтому в тексте — как отправить заново.
        text, keyboard = await build_unsupported_screen()
        await render_screen(update, context, text, keyboard)
        return

    attachment_label = MEDIA_KINDS[media[0]] if media is not None else None
    mention = _mention(update.effective_user)
    ticket, created = await sync_to_async(open_ticket)(user.id, mention, message_text or f'[{attachment_label}]')

    if created:
        if not await _start_topic(context, ticket, mention):
            text, keyboard = await build_unavailable_screen()
            await render_screen(update, context, text, keyboard)
            return

    await _post_to_topic(context, ticket, mention, message_text, attachment_label=attachment_label)

    delivered = True
    if media is not None:
        delivered = await _relay_media(
            context, media, chat_id=settings.SUPPORT_CHAT_ID, message_thread_id=ticket.topic_id
        )
        if not delivered:
            # Оператор уже прочитал строку «Вложение: фотография» — без этой
            # приписки он будет ждать файл, которого не будет.
            await _note_in_topic(context, ticket, f'Вложение ({attachment_label}) не дошло.')

    text, keyboard = await build_sent_screen(ticket.id, attachment_lost=not delivered)
    await render_screen(update, context, text, keyboard)


async def _start_topic(context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, mention: str) -> bool:
    """Завести тему и привязать её к тикету. ``False`` — Telegram отказал.

    Тема привязывается сразу после создания, до первого сообщения в ней: тикет
    без темы нельзя ни закрыть, ни продолжить, и он навсегда занимает
    единственный открытый слот пользователя. Поэтому отказ в создании темы
    обязан убрать черновик, а всё, что после — уже поправимо.
    """
    try:
        topic = await context.bot.create_forum_topic(
            chat_id=settings.SUPPORT_CHAT_ID, name=_topic_name(ticket.id, mention)
        )
    except TelegramError:
        await sync_to_async(discard_draft)(ticket.id)
        return False

    ticket.topic_id = topic.message_thread_id
    await sync_to_async(attach_topic)(ticket.id, topic.message_thread_id)
    return True


async def _post_to_topic(
    context: ContextTypes.DEFAULT_TYPE,
    ticket: SupportTicket,
    mention: str,
    message_text: str,
    *,
    attachment_label: str | None = None,
) -> None:
    state = [f'От: {html.escape(mention)}']
    if attachment_label is not None:
        state.append(f'Вложение: {attachment_label}')

    text = screen(f'Обращение № {ticket.id}', state=state, body=[html.escape(message_text)])
    posted = await context.bot.send_message(
        chat_id=settings.SUPPORT_CHAT_ID,
        message_thread_id=ticket.topic_id,
        text=text,
        reply_markup=_topic_keyboard(ticket),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )
    await sync_to_async(remember_topic_message)(ticket.id, posted.message_id)


async def _note_in_topic(context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, note: str) -> None:
    """Служебная строка в теме — то, что оператор обязан узнать без кнопок."""
    await context.bot.send_message(
        chat_id=settings.SUPPORT_CHAT_ID,
        message_thread_id=ticket.topic_id,
        text=screen(note),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )


async def _claim_for_operator(context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, telegram_user) -> str:
    """Закрепить обращение за ответившим и вернуть его подпись для клиента.

    Обращение достаётся первому ответившему — отдельная кнопка «взять в работу»
    была бы шагом, который команда из двух человек пропустит, а ответ и так
    происходит ровно один раз и в нужный момент. Второй оператор ничего не
    перехватывает: его сообщение уходит клиенту подписанным его именем, а тема
    продолжает показывать, за кем обращение числится. Кто именно написал в теме,
    операторы видят в самом Telegram, поэтому дублировать это ботом нечем.
    """
    operator = _operator_name(telegram_user)
    operator_id = getattr(telegram_user, 'id', None)
    if operator_id is None:
        return operator

    if not await sync_to_async(claim_ticket)(ticket.id, operator_id, operator):
        return operator

    ticket.operator_name = operator
    ticket.operator_telegram_id = operator_id

    if ticket.topic_id is None:
        return operator

    try:
        await context.bot.edit_forum_topic(
            chat_id=settings.SUPPORT_CHAT_ID,
            message_thread_id=ticket.topic_id,
            name=_topic_name(ticket.id, ticket.telegram_username, operator=operator),
        )
        await _note_in_topic(context, ticket, f'Обращение принял: {operator}')
    except TelegramError:
        # Тема могла быть удалена или закрыта вручную. Захват уже записан, и
        # ответ клиенту важнее, чем заголовок темы.
        pass

    return operator


async def support_operator_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сообщение из темы чата операторов — обратно клиенту."""
    message = update.message
    if message.from_user is not None and message.from_user.is_bot:
        # Собственные публикации бота в теме — не ответ оператора.
        return

    if message.message_thread_id is None:
        return

    ticket = await sync_to_async(ticket_by_topic)(message.message_thread_id)
    if ticket is None:
        # Тема закрытого обращения остаётся в чате и остаётся доступной для
        # письма. Для клиента разговор закончен, и продолжать его тут нечем.
        return

    reply_text = truncate(message.text or message.caption)
    media = _extract_media(message)
    if media is None and not reply_text:
        await _note_in_topic(context, ticket, 'Такое вложение клиенту не уйдёт — оно не отправлено')
        return

    operator = await _claim_for_operator(context, ticket, message.from_user)

    state = [f'Обращение № {ticket.id}', f'Оператор: {html.escape(operator)}']
    if media is not None:
        state.append(f'Вложение: {MEDIA_KINDS[media[0]]}')

    text = screen('Ответ поддержки', state=state, body=[html.escape(reply_text)])
    await context.bot.send_message(
        chat_id=ticket.user.telegram_id,
        text=text,
        reply_markup=_reply_keyboard(),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )

    if media is not None and not await _relay_media(context, media, chat_id=ticket.user.telegram_id):
        await _note_in_topic(context, ticket, 'Вложение не дошло до клиента — отправьте его ещё раз')


async def support_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка оператора «Закрыть обращение»."""
    query = update.callback_query
    ticket_id = int(query.data.split(':')[1])

    ticket = await sync_to_async(close_ticket)(ticket_id, _mention(query.from_user))
    if ticket is None:
        await query.answer('Обращение уже закрыто.')
        return

    await query.answer('Обращение закрыто.')

    if ticket.topic_id is not None:
        await context.bot.edit_forum_topic(
            chat_id=settings.SUPPORT_CHAT_ID,
            message_thread_id=ticket.topic_id,
            name=_topic_name(ticket.id, ticket.telegram_username, operator=ticket.operator_name, closed=True),
        )
        await context.bot.send_message(
            chat_id=settings.SUPPORT_CHAT_ID,
            message_thread_id=ticket.topic_id,
            text=screen(
                f'Обращение № {ticket.id} закрыто',
                state=[f'Закрыл: {html.escape(_mention(query.from_user))}'],
            ),
            parse_mode=PARSE_MODE,
            link_preview_options=NO_PREVIEW,
        )

    text = screen(
        'Обращение закрыто',
        state=[f'Обращение № {ticket.id}'],
        body=['Если вопрос остался, откройте новое обращение кнопкой «Поддержка».'],
    )
    await context.bot.send_message(
        chat_id=ticket.user.telegram_id,
        text=text,
        reply_markup=_reply_keyboard(),
        parse_mode=PARSE_MODE,
        link_preview_options=NO_PREVIEW,
    )
