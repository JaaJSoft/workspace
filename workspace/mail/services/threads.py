"""Email threading: reconstruct a thread from stored messages, and
compute the headers that keep an outgoing reply attached to one.

Used by the LLM event-extraction worker to feed the model the
full conversation context. Capped at max_depth so a pathological
500-reply chain does not silently multiply the LLM bill.
"""

from ..models import MailMessage

# RFC 5322 has no hard limit on References, but an unbounded chain grows one
# id per hop forever. Trimming the middle is what MUAs do: the root anchors
# the thread for clients that group on it, the tail carries the recent hops.
MAX_REFERENCES = 20


def get_thread(message: MailMessage, max_depth: int = 20) -> list[MailMessage]:
    """Return ancestors of `message` in chronological order, ending
    with `message` itself.

    The walk starts from `message.in_reply_to`, looks up a MailMessage
    in the same account whose `message_id` matches, then continues
    upward until in_reply_to is empty, no parent matches in our DB,
    or the chain has reached max_depth total messages (the starting
    message counts toward the cap).

    A solo message (no in_reply_to, or in_reply_to points to an
    unknown id) returns [message]. The thread is always at least
    `message` itself.
    """
    chain = [message]
    current = message
    visited = {message.pk}
    while len(chain) < max_depth and current.in_reply_to:
        parent = MailMessage.objects.filter(
            account=message.account, message_id=current.in_reply_to
        ).first()
        # A pathological cycle (A -> B -> A, or A -> A) would otherwise
        # oscillate until max_depth and feed the LLM a duplicated thread.
        if parent is None or parent.pk in visited:
            break
        visited.add(parent.pk)
        chain.append(parent)
        current = parent

    chain.reverse()
    return chain


def reply_headers(account, parent_uuid) -> tuple[str, str]:
    """Return the (In-Reply-To, References) pair for a reply to `parent_uuid`.

    The parent is resolved against `account` and its stored Message-ID is
    used - the caller must never pass a header value straight from a client,
    which would let anyone graft a reply onto an arbitrary thread.

    Returns ("", "") when there is no parent, the parent belongs to another
    account or is deleted, or it carries no Message-ID: an unthreaded reply
    beats one anchored to an id we cannot vouch for.
    """
    if not parent_uuid:
        return "", ""

    parent = MailMessage.objects.filter(
        account=account, uuid=parent_uuid, deleted_at__isnull=True
    ).first()
    if parent is None or not parent.message_id:
        return "", ""

    refs = list(dict.fromkeys(parent.references.split() + [parent.message_id]))
    if len(refs) > MAX_REFERENCES:
        refs = refs[:1] + refs[-(MAX_REFERENCES - 1) :]
    return parent.message_id, " ".join(refs)
