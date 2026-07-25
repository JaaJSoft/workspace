from workspace.common.search import apply_fulltext
from workspace.common.search.schema import Col, FulltextIndex

from ..models import Message
from .conversations import user_conversation_ids

CHAT_FTS = FulltextIndex(
    table="chat_message",
    columns=(Col("body", cap=100_000),),
)


def fts_messages(qs, query):
    """Apply chat full-text search to a Message queryset.

    Returns qs filtered to matches and annotated with `search_rank`.
    Caller applies order_by.
    """
    return apply_fulltext(qs, query, index=CHAT_FTS)


def search_messages_qs(user, query, *, conversation_id=None):
    """Ranked full-text message search, access-filtered for `user`.

    Single source for every message-search surface (conversation view,
    AI tool, global search). Membership is enforced here even when a
    conversation_id scope is given, so callers cannot leak other users'
    conversations. Callers may add their own extra filters on top.
    """
    qs = Message.objects.filter(
        conversation_id__in=user_conversation_ids(user),
        deleted_at__isnull=True,
    )
    if conversation_id is not None:
        qs = qs.filter(conversation_id=conversation_id)
    return fts_messages(qs, query).order_by("-search_rank", "-created_at")
