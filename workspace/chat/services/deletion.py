"""What a soft-deleted message leaves behind, and how it is cleaned up.

Deleting a message only stamps ``deleted_at``: the row stays so the tombstone,
the reply quotes and the thread structure keep resolving, which also means no
``on_delete=CASCADE`` ever fires. Everything a member could still reach through
that row has to be erased by hand - that is what this module does.
"""

import logging

from django.core.files.storage import default_storage
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


def purge_message_content(message):
    """Erase what a message being soft-deleted still exposes.

    That is its own text and the rows and blobs hanging off it. The Message
    row survives, stripped: the placeholder needs its author and timestamp,
    the quote blocks need its uuid, and the thread structure needs its
    ``reply_to``/``thread_root`` pointers. So do its ThreadParticipant rows -
    a deleted root still owns live replies, whose read state goes with them -
    and the shared ``LinkPreview`` cache, keyed by URL and owned by no message.
    """
    message.body = ""
    message.body_html = ""
    message.tool_data = None
    message.save(update_fields=["body", "body_html", "tool_data"])

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
    transaction.on_commit(lambda: discard_attachment_files(attachments))


def discard_attachment_files(attachments):
    """Drop attachment blobs and their metadata memo, best effort.

    Call from ``transaction.on_commit``: neither storage nor cache rolls back,
    so both must only be touched once the rows are really gone.
    """
    for attachment in attachments:
        # The download view memoises attachment metadata for 60s under this
        # tag. Bumping it is what makes the deleted row authoritative: without
        # it the 404 would only come from the blob having vanished below, so a
        # storage backend that refuses or defers the delete keeps serving it.
        invalidate_tags(f"att:{attachment.uuid}")
        if attachment.file:
            delete_attachment_blob(attachment.file.name)


def delete_attachment_blob(name):
    try:
        default_storage.delete(name)
    except OSError:
        logger.warning("Could not delete chat attachment %s", scrub(name))


def purge_deleted_message_backlog(
    *, messages, attachments, reactions, link_previews, interactions, pins
):
    """Apply the purge to messages deleted before the purge existed.

    Called by migration 0029, which passes the historical models from the app
    registry; the tests pass the real ones. The body must therefore keep
    working against historical model classes: fields and managers only, no
    model methods and no properties.

    It deliberately skips the attachment metadata memo the live purge bumps.
    Reaching the cache would make `migrate` fail whenever Redis is unavailable,
    and a 60s memo self-heals long before anyone notices.
    """
    deleted = {"message__deleted_at__isnull": False}

    blob_names = [
        name
        for name in attachments.objects.filter(**deleted).values_list("file", flat=True)
        if name
    ]

    attachments.objects.filter(**deleted).delete()
    reactions.objects.filter(**deleted).delete()
    link_previews.objects.filter(**deleted).delete()
    interactions.objects.filter(**deleted).delete()
    pins.objects.filter(**deleted).delete()
    messages.objects.filter(deleted_at__isnull=False).update(
        body="", body_html="", tool_data=None
    )

    transaction.on_commit(lambda: [delete_attachment_blob(name) for name in blob_names])
