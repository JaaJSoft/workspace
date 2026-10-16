"""Pinned folders: which pins a user still sees, and dropping them on departure."""

from django.db.models import Exists, OuterRef

from ..models import FileFavorite, PinnedFolder
from .files import FileService


def visible_pins(user):
    """Return *user*'s pins on live folders they can still open, in pin order.

    A pin outlives the access it was made under: a group member who leaves
    keeps the row, and a share can be revoked. Without the access check the
    sidebar would keep naming a folder the user can no longer open - under
    whatever name its owners give it next.

    Each pin carries ``_folder_is_favorite`` and has ``folder`` loaded.
    """
    return (
        PinnedFolder.objects.filter(
            owner=user,
            folder_id__in=FileService.accessible_file_ids(user, include_deleted=False),
        )
        .select_related("folder")
        .annotate(
            _folder_is_favorite=Exists(
                FileFavorite.objects.filter(owner=user, file_id=OuterRef("folder_id"))
            ),
        )
        .order_by("position", "created_at")
    )


def remove_group_pins(*, user_ids, group_ids):
    """Delete the pins *user_ids* made on folders of *group_ids*.

    For members leaving those groups, like the link and share revocations in
    ``sharing``. Returns the number of pins deleted.
    """
    deleted, _ = PinnedFolder.objects.filter(
        owner_id__in=user_ids, folder__group_id__in=group_ids
    ).delete()
    return deleted
