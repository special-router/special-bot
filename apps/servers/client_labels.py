"""The one place that decides what label a 3x-ui client carries.

xray keys its traffic statistics by ``email``, so a client written with an empty
one accumulates nothing at all.  The label is derived from ``UserVPN.id``: a
surrogate key that means nothing to whoever reads the panel, unlike a Telegram
id, username or address.  It is scoped by inbound because
``client_traffics.email`` is UNIQUE across the whole panel database, so the same
UUID appearing on a mirror inbound would otherwise want the same row.

``LabelledClientApi`` stamps it inside the panel transport, so no caller has to
remember it.  Nothing at all is written while ``CLIENT_TRAFFIC_LABELS_ENABLED``
is off, which is the default and reproduces the historical behaviour byte for
byte.  With it on, each write to an inbound resolves to one of three outcomes:

* the inbound is the one its owner's ``Server`` row configures -- write the
  label;
* it is not, and the client carries one of our labels -- **clear it**, because
  callers reuse one ``Client`` object across inbounds and a label must never
  travel to an inbound it was not issued for;
* it is not, and the value is foreign or empty -- leave it exactly as found.
  The status inbound's ``осталось N дней`` and any hand-set value are safe.

The panel hosts a foreign tenant on inbounds 10 and 13, and a label on their
inbound is a write we had no business making.
"""
from __future__ import annotations

import re
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from py3xui.async_api import AsyncClientApi


LABEL_PREFIX: str = 'uv'

_LABEL_PATTERN = re.compile(rf'^{LABEL_PREFIX}-\d+-\d+$')


def client_label(inbound_id: int, user_vpn_id: int) -> str:
    """Return the attribution label for one connection on one inbound."""
    return f'{LABEL_PREFIX}-{int(inbound_id)}-{int(user_vpn_id)}'


def is_client_label(value: str | None) -> bool:
    """Report whether a label was written by this module."""
    return bool(_LABEL_PATTERN.match(value or ''))


def labelling_enabled() -> bool:
    """Report whether labels may be written at all."""
    return bool(getattr(settings, 'CLIENT_TRAFFIC_LABELS_ENABLED', False))


def owner_for_uuid(client_uuid: Any) -> tuple[int, int] | None:
    """Resolve a panel client UUID to ``(UserVPN.id, its server's inbound id)``.

    Returns None when the client has no owner we can name.  Deferred import:
    the panel transport in ``utils`` reaches this module, and ``utils`` must not
    depend on Django models at import time.
    """
    from apps.vpn.models import UserVPN

    if not client_uuid:
        return None
    try:
        owner = (
            UserVPN.objects
            .filter(vpn_uuid=str(client_uuid))
            .order_by('id')
            .values_list('id', 'server__inbound_id')
            .first()
        )
    except (ValidationError, ValueError, TypeError):
        # Not every panel client id is a UUID; a foreign one simply has no owner.
        return None
    if owner is None or owner[1] is None:
        return None
    return int(owner[0]), int(owner[1])


def label_for_client(client: Any, inbound_id: int | None) -> str | None:
    """Return the label to write, or None to leave the client's email alone.

    A label is written only on an inbound we own, which is the primary inbound
    configured on the owner's ``Server`` row and nothing else.  Inbounds 10 and
    13 carry a foreign tenant's clients; the status and mirror inbounds are a
    different record of the same customer.  Anything this function cannot
    positively establish as ours is left unlabelled.
    """
    if not labelling_enabled():
        return None
    if not inbound_id:
        return None
    current = getattr(client, 'email', '') or ''
    if current and not is_client_label(current):
        return None
    owner = owner_for_uuid(getattr(client, 'id', None))
    if owner is None:
        return None
    user_vpn_id, primary_inbound_id = owner
    if primary_inbound_id != int(inbound_id):
        return None
    return client_label(inbound_id, user_vpn_id)


_alabel_for_client = sync_to_async(label_for_client)


class LabelledClientApi(AsyncClientApi):
    """Panel client API that labels every client it writes.

    Both routes address a client by UUID and carry ``email`` only inside the
    serialized body, so stamping it changes nothing about how a client is
    reached.
    """

    async def add(self, inbound_id: int, clients: list) -> None:
        for client in clients:
            await self._stamp(client, inbound_id)
        await super().add(inbound_id, clients)

    async def update(self, client_uuid: str, client: Any) -> None:
        await self._stamp(client, getattr(client, 'inbound_id', None))
        await super().update(client_uuid, client)

    @staticmethod
    async def _stamp(client: Any, inbound_id: int | None) -> None:
        """Decide this client's ``email`` for this inbound, in three branches.

        Callers reuse one ``Client`` object across several inbounds, so a label
        earned on the primary inbound would otherwise ride along to a mirror and
        claim the primary's ``client_traffics`` row -- the panel's UNIQUE on
        ``email`` is global.  A label we did not authorise for the inbound being
        written is therefore removed rather than merely left unwritten.
        """
        if not labelling_enabled():
            # Off is a complete no-op: the field is not ours to manage at all.
            return
        label = await _alabel_for_client(client, inbound_id)
        if label is not None:
            client.email = label
        elif inbound_id and is_client_label(getattr(client, 'email', '') or ''):
            # Ours, but not for this inbound.  A write with no inbound to
            # compare against is left alone: not knowing is not grounds to
            # discard a valid label.
            client.email = ''
