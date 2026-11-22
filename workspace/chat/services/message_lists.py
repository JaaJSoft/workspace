"""The server-rendered message lists: a conversation's flow and a thread.

Both page with the shared ``OptInLimitOffsetPagination``, oldest message
first. Counting from the oldest end keeps every offset still while the
conversation lives: a new message lands past the end, and a deleted one stays
in the flow as its placeholder. Offsets counted from the newest end would move
by one on every arrival, and the next older page would repeat a message.

A request without an ``offset`` opens the list on its last page, or on the
page holding ``?message=`` when that message is in the list.
"""

from django.db.models import Q
from rest_framework.request import Request

from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.common.uuids import parse_uuid_or_none

PAGE_SIZE = 50


def find_message(scope, raw):
    """The message ``raw`` names within *scope*, or None.

    A message outside *scope* - another conversation, another thread - is None
    exactly like one that does not exist, so a link never tells them apart.
    """
    uuid = parse_uuid_or_none(raw)
    if uuid is None:
        return None
    return (
        scope.filter(uuid=uuid)
        .only("uuid", "created_at", "thread_root_id", "conversation_id")
        .first()
    )


def _opening_offset(qs, anchor):
    def offset(limit):
        if anchor is None:
            return max(0, qs.count() - limit)
        older = qs.filter(
            Q(created_at__lt=anchor.created_at)
            | Q(created_at=anchor.created_at, uuid__lt=anchor.uuid)
        ).count()
        return max(0, older - limit // 2)

    return offset


def page_messages(request, qs, anchor=None):
    """One page of *qs* in chronological order, and the paginator that cut it.

    ``anchor`` is the message a list without an ``offset`` opens on, half a
    page from the top; without one it opens on the last page.
    """
    qs = qs.order_by("created_at", "uuid")
    paginator = OptInLimitOffsetPagination()
    # The partial renders a page, never a whole conversation.
    paginator.default_limit = PAGE_SIZE
    page = paginator.paginate_queryset(
        qs, Request(request), default_offset=_opening_offset(qs, anchor)
    )
    return page, paginator
