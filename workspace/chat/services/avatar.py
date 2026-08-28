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

    A direct message reads its partner off the instance, so a caller that
    prefetched the members filtered to the active ones gets those.

    Both rendering paths go through this: the sidebar row renders it into the
    markup, the API sends it as ``avatar_initial`` for the header and the info
    panel. Computing it twice is what let them disagree.
    """
    partner = next(
        (m.user for m in conversation.members.all() if m.user_id != viewer.id), None
    )
    return avatar_initial_for(conversation.kind, conversation.title, partner)


def avatar_initial_for(kind, title, partner=None) -> str:
    """The initials for a conversation of *kind* named *title*.

    A direct message shows the one letter its *partner* is drawn with, matching
    <user-avatar>. A group shows the first letter of the first two words of its
    name - the same name the row is labelled with, so the circle and the label
    can no longer describe different people.

    Every group carries a name (``default_group_title`` fills one in when the
    creator gives none), so the ``"G"`` here is a guard for rows predating that,
    not a branch worth designing around.
    """
    if kind == Conversation.Kind.DM:
        return _initial(partner) if partner else "?"
    return _name_initials(title) or "G"


def _name_initials(name) -> str:
    """The first letter of each of *name*'s first two parts.

    Parts are separated by commas when the name lists several people ("Sam
    Rivera, Jordan Lee" -> SJ) and by spaces otherwise ("Product Launch" -> PL).
    A generated title is a list of names, so it is still lettered one person at
    a time rather than twice from whoever comes first.
    """
    parts = name.split(",") if "," in name else name.split()
    return "".join(part.strip()[:1].upper() for part in parts[:2] if part.strip())


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
