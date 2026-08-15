"""Group conversation avatar processing and storage service.

Group avatars are stored at ``avatars/groups/{uuid}.webp`` using
Django's ``default_storage``.  Presence is tracked via the
``Conversation.has_avatar`` boolean field.
"""

from __future__ import annotations

import logging

from workspace.common.services.image import (
    delete_image,
    get_image_etag,
    process_image_to_webp,
    save_image,
)

from ..models import Conversation

logger = logging.getLogger(__name__)


def conversation_avatar_initial(conversation, viewer) -> str:
    """The letters drawn when *conversation* has no uploaded avatar.

    One letter for a direct message (the other participant), up to two for a
    group (its first two other members). Reads the members off the instance,
    so a caller that prefetched them filtered to the active ones gets those.

    Both rendering paths go through this: the sidebar row renders it into the
    markup, the API sends it as ``avatar_initial`` for the header and the info
    panel. Computing it twice is what let them disagree.
    """
    others = [m.user for m in conversation.members.all() if m.user_id != viewer.id]
    if conversation.kind == Conversation.Kind.DM:
        return _initial(others[0]) if others else "?"
    return "".join(_initial(user) for user in others[:2]) or "G"


def _initial(user) -> str:
    return (user.get_full_name() or user.username)[:1].upper()


def get_group_avatar_path(conversation_uuid) -> str:
    """Return the storage path for a group conversation's avatar."""
    return f"avatars/groups/{conversation_uuid}.webp"


def has_group_avatar(conversation) -> bool:
    """Check whether *conversation* has an uploaded avatar."""
    return conversation.has_avatar


def process_and_save_group_avatar(
    conversation,
    image_file,
    crop_x: float,
    crop_y: float,
    crop_w: float,
    crop_h: float,
) -> None:
    """Process an uploaded image and save it as the group's avatar."""
    image_bytes = process_image_to_webp(image_file, crop_x, crop_y, crop_w, crop_h)
    path = get_group_avatar_path(conversation.uuid)
    save_image(path, image_bytes)
    conversation.has_avatar = True
    conversation.save(update_fields=["has_avatar"])
    logger.info("Group avatar saved for conversation %s", conversation.uuid)


def delete_group_avatar(conversation) -> None:
    """Delete the group's avatar file and clear the flag."""
    path = get_group_avatar_path(conversation.uuid)
    delete_image(path)
    conversation.has_avatar = False
    conversation.save(update_fields=["has_avatar"])
    logger.info("Group avatar deleted for conversation %s", conversation.uuid)


def get_group_avatar_etag(conversation_uuid) -> str | None:
    """Return an ETag string based on the file's modification time, or *None*."""
    path = get_group_avatar_path(conversation_uuid)
    return get_image_etag(path)
