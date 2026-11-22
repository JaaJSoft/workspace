"""Keyset cursor of the notification list.

The cursor carries the ``(created_at, uuid)`` of the last row the client saw,
not a reference to that row: a merge moves a row to the present, a read takes
it out of the Unread filter and a delete removes it, and a boundary read back
from the row would move or vanish with it.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from django.db.models import Q

from workspace.common.uuids import UUID_PATTERN

ORDERING = ("-created_at", "-uuid")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_CURSOR_RE = re.compile(rf"^(?P<micros>-?\d{{1,20}})\.(?P<uuid>{UUID_PATTERN})$")


def encode_cursor(notification):
    """The position right after *notification*, as a query-string token."""
    micros = (notification.created_at - _EPOCH) // timedelta(microseconds=1)
    return f"{micros}.{notification.uuid}"


def parse_cursor(raw):
    """Decode an ``encode_cursor`` token into ``(created_at, uuid)``.

    ValueError when it is not one.
    """
    match = _CURSOR_RE.match(raw or "")
    if match is None:
        raise ValueError("malformed cursor")
    try:
        created_at = _EPOCH + timedelta(microseconds=int(match["micros"]))
    except OverflowError as exc:
        raise ValueError("cursor out of range") from exc
    return created_at, uuid.UUID(match["uuid"])


def after_cursor_q(created_at, after_uuid):
    """The rows strictly after the cursor in ``ORDERING``."""
    return Q(created_at__lt=created_at) | Q(created_at=created_at, uuid__lt=after_uuid)
