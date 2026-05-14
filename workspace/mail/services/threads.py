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
    or max_depth ancestors have been collected.

    A solo message (no in_reply_to, or in_reply_to points to an
    unknown id) returns [message]. The thread is always at least
    `message` itself.
    """
    chain = [message]
    current = message
    while (
        len(chain) < max_depth
        and current.in_reply_to
    ):
        parent = (
            MailMessage.objects
            .filter(account=message.account, message_id=current.in_reply_to)
            .first()
        )
        if parent is None or parent.pk == current.pk:
            break
        chain.append(parent)
        current = parent

    chain.reverse()
    return chain
