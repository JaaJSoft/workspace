"""Reconstruct an email thread by walking In-Reply-To upward.

Used by the LLM event-extraction worker to feed the model the
full conversation context. Capped at max_depth so a pathological
500-reply chain does not silently multiply the LLM bill.
"""

from ..models import MailMessage


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
    while (
        len(chain) < max_depth
        and current.in_reply_to
    ):
        parent = (
            MailMessage.objects
            .filter(account=message.account, message_id=current.in_reply_to)
            .first()
        )
        # A pathological cycle (A -> B -> A, or A -> A) would otherwise
        # oscillate until max_depth and feed the LLM a duplicated thread.
        if parent is None or parent.pk in visited:
            break
        visited.add(parent.pk)
        chain.append(parent)
        current = parent

    chain.reverse()
    return chain
