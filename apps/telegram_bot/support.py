"""Состояние обращений в поддержку: всё, что должно пережить перезапуск бота.

Здесь нет ни одного вызова Bot API — только база. Разделение не косметическое:
единственный открытый тикет на пользователя держится частичным уникальным
индексом, и код обязан уметь проиграть эту гонку и подобрать чужую строку
вместо своей. Обработчики этим не занимаются.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.telegram_bot.models import SupportPrompt, SupportTicket


# Предел Bot API — 4096 символов на сообщение. Обрезаем с запасом на заголовок
# темы и служебные строки, которые бот добавляет к тексту пользователя.
MESSAGE_MAX_LENGTH = 3000


def truncate(text: str, limit: int = MESSAGE_MAX_LENGTH) -> str:
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def open_prompt(user_id: int) -> None:
    """Отметить, что от пользователя ждут текст обращения."""
    SupportPrompt.objects.update_or_create(user_id=user_id)


def consume_prompt(user_id: int) -> bool:
    """Снять ожидание и сказать, было ли оно.

    Удаление — один оператор в базе, поэтому из двух сообщений, пришедших
    одновременно, строку заберёт ровно одно. Это и есть вся защита от спама:
    каждое следующее обращение требует нового нажатия кнопки.
    """
    deleted, _ = SupportPrompt.objects.filter(user_id=user_id).delete()
    return bool(deleted)


def open_ticket(user_id: int, username: str, subject: str) -> tuple[SupportTicket, bool]:
    """Вернуть открытый тикет пользователя, заведя его, если открытого нет.

    Второй элемент — признак того, что строка создана здесь и темы у неё пока
    нет. Вставка обёрнута во вложенный ``atomic``: на PostgreSQL пойманная
    ``IntegrityError`` без точки сохранения оставляет транзакцию сломанной, и
    следующий же запрос падает. На SQLite это незаметно, поэтому проверяется
    чтением кода, а не тестом.
    """
    existing = SupportTicket.objects.filter(user_id=user_id, status=SupportTicket.STATUS_OPEN).first()
    if existing is not None:
        return existing, False

    try:
        with transaction.atomic():
            ticket = SupportTicket.objects.create(
                user_id=user_id,
                telegram_username=username,
                subject=truncate(subject, SupportTicket.SUBJECT_MAX_LENGTH),
            )
    except IntegrityError:
        # Гонку выиграл соседний воркер: у пользователя уже есть открытый тикет,
        # и правильный ответ — писать в него, а не отказывать в обращении.
        return SupportTicket.objects.get(user_id=user_id, status=SupportTicket.STATUS_OPEN), False

    return ticket, True


def discard_draft(ticket_id: int) -> None:
    """Убрать строку, для которой так и не завелась тема.

    Тикет без темы занимает единственный открытый слот пользователя и при этом
    не закрывается: закрытие переименовывает тему, а переименовывать нечего.
    Условие по ``topic_id`` защищает от удаления живого обращения.
    """
    SupportTicket.objects.filter(pk=ticket_id, topic_id__isnull=True).delete()


def attach_topic(ticket_id: int, topic_id: int) -> None:
    SupportTicket.objects.filter(pk=ticket_id).update(topic_id=topic_id)


def remember_topic_message(ticket_id: int, message_id: int) -> None:
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if ticket is None:
        return
    ticket.meta = {**(ticket.meta or {}), 'topic_message_id': message_id}
    ticket.save(update_fields=['meta'])


def ticket_by_topic(topic_id: int) -> SupportTicket | None:
    """Найти тикет по теме — только среди открытых.

    Закрытая тема остаётся в чате операторов и остаётся доступной для письма.
    Ответ, набранный в ней, не должен уйти клиенту: для него разговор закончен.
    """
    return (
        SupportTicket.objects.select_related('user')
        .filter(topic_id=topic_id, status=SupportTicket.STATUS_OPEN)
        .first()
    )


def close_ticket(ticket_id: int, closed_by: str) -> SupportTicket | None:
    """Закрыть тикет. ``None``, если его уже закрыли — второе нажатие не событие."""
    closed = SupportTicket.objects.filter(pk=ticket_id, status=SupportTicket.STATUS_OPEN).update(
        status=SupportTicket.STATUS_CLOSED, closed_at=timezone.now()
    )
    if not closed:
        return None

    ticket = SupportTicket.objects.select_related('user').get(pk=ticket_id)
    ticket.meta = {**(ticket.meta or {}), 'closed_by': closed_by}
    ticket.save(update_fields=['meta'])
    return ticket
