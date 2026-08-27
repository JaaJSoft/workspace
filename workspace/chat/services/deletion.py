"""What a soft-deleted message leaves behind, and how it is cleaned up.

Deleting a message only stamps ``deleted_at``: the row stays so the tombstone,
the reply quotes and the thread structure keep resolving, which also means no
``on_delete=CASCADE`` ever fires. Everything a member could still reach through
that row has to be erased by hand - that is what this module does.
"""

import logging

from django.db import transaction

from workspace.common.cache import invalidate_tags
from workspace.common.logging import scrub

from ..models import (
    MessageAttachment,
    MessageInteraction,
    MessageLinkPreview,
    PinnedMessage,
    Reaction,
)
from .reactions import invalidate_quick_reactions

logger = logging.getLogger(__name__)


def purge_message_dependents(message):
    """Erase the rows and blobs hanging off a message being soft-deleted.

    Kept on purpose: the Message row itself, its ``reply_to``/``thread_root``
    pointers, its ThreadParticipant rows (a deleted root still owns live
    replies, whose read state must survive with them) and the shared
    ``LinkPreview`` cache, which is keyed by URL and belongs to no message.
    """
    attachments = list(MessageAttachment.objects.filter(message=message))
    reactor_ids = set(
        Reaction.objects.filter(message=message).values_list("user_id", flat=True)
    )

    MessageAttachment.objects.filter(message=message).delete()
    Reaction.objects.filter(message=message).delete()
    MessageLinkPreview.objects.filter(message=message).delete()
    MessageInteraction.objects.filter(message=message).delete()
    PinnedMessage.objects.filter(message=message).delete()

    for user_id in reactor_ids:
        invalidate_quick_reactions(user_id)

    # Storage and cache are not transactional: touch them only once the rows
    # are really gone, or a rollback leaves live rows pointing at nothing.
    transaction.on_commit(lambda: _discard_attachment_files(attachments))


def _discard_attachment_files(attachments):
    for attachment in attachments:
        # The download view memoises attachment metadata for 60s under this
        # tag; without the bump the blob stays served after the row is gone.
        invalidate_tags(f"att:{attachment.uuid}")
        if not attachment.file:
            continue
        try:
            attachment.file.delete(save=False)
        except OSError:
            logger.warning(
                "Could not delete chat attachment %s", scrub(attachment.file.name)
            )
