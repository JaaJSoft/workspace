"""Cursor pagination of a message list, in both directions.

A page is cut from a queryset of the messages a surface shows (the main flow,
or a thread's replies) and positioned by one cursor, always a message of that
surface resolved by ``find_cursor``:

- no cursor: the latest messages;
- ``before``: the messages older than the cursor, for "load older";
- ``after``: the messages newer than the cursor, back down to the present;
- ``around``: the cursor and the messages on both sides, for a link to it.

``has_older`` and ``has_newer`` are ``None`` when the page says nothing about
that side: an older page does not know whether the reader is at the present,
and the client keeps what it knew.
"""

from dataclasses import dataclass

from workspace.common.uuids import parse_uuid_or_none

PAGE_SIZE = 50
CURSORS = ("around", "after", "before")


@dataclass
class MessagePage:
    messages: list
    has_older: bool | None
    has_newer: bool | None
    # The cursor the page was cut from, or None for the latest page.
    direction: str | None = None
    cursor: object = None


def find_cursor(scope, params):
    """The cursor named by the request, as ``(direction, message)``.

    The first of ``around``, ``after`` and ``before`` present in *params*
    decides. Its message is looked up in *scope* only: a message elsewhere -
    another conversation, another thread - resolves to None exactly like one
    that does not exist, so a cursor can never read anything about a row the
    caller cannot see.
    """
    for direction in CURSORS:
        raw = params.get(direction)
        if not raw:
            continue
        uuid = parse_uuid_or_none(raw)
        if uuid is None:
            return direction, None
        cursor = (
            scope.filter(uuid=uuid)
            .only("uuid", "created_at", "thread_root_id", "conversation_id")
            .first()
        )
        return direction, cursor
    return None, None


def message_page(qs, direction=None, cursor=None, limit=PAGE_SIZE):
    """One page of *qs* in chronological order, positioned by *cursor*.

    An unresolved cursor (None) gives the latest page, as an unknown or
    malformed one always has.
    """
    if cursor is None:
        page = list(qs.order_by("-created_at")[: limit + 1])
        has_older = len(page) > limit
        return MessagePage(page[:limit][::-1], has_older, False)

    if direction == "before":
        page = list(
            qs.filter(created_at__lt=cursor.created_at).order_by("-created_at")[
                : limit + 1
            ]
        )
        return MessagePage(
            page[:limit][::-1], len(page) > limit, None, direction, cursor
        )

    if direction == "after":
        page = list(
            qs.filter(created_at__gt=cursor.created_at).order_by("created_at")[
                : limit + 1
            ]
        )
        return MessagePage(page[:limit], None, len(page) > limit, direction, cursor)

    # around: the cursor and its newer neighbours on one side, the older ones
    # on the other. Only the keys are read twice, so the page itself - and
    # every prefetch hanging off it - is loaded in one go.
    older_count = limit // 2
    newer_count = limit - older_count
    older = list(
        qs.filter(created_at__lt=cursor.created_at)
        .order_by("-created_at")
        .values_list("pk", flat=True)[: older_count + 1]
    )
    newer = list(
        qs.filter(created_at__gte=cursor.created_at)
        .order_by("created_at")
        .values_list("pk", flat=True)[: newer_count + 1]
    )
    keys = older[:older_count] + newer[:newer_count]
    page = list(qs.filter(pk__in=keys).order_by("created_at"))
    return MessagePage(
        page, len(older) > older_count, len(newer) > newer_count, direction, cursor
    )
