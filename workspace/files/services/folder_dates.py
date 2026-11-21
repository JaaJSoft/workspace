"""The modification date of a folder: when its list of entries last changed.

A folder is dated like a directory on disk. Adding, removing or renaming one
of its entries changes it, writing the bytes of a file inside does not: the
file carries its own date, and a folder holding a note that autosaves would
otherwise always read as modified a moment ago. Only the direct parent moves,
never its ancestors: dating the whole chain would make every upload write as
many rows as the folder is deep, and serialize every write of a tree on its
root row.
"""

from django.utils import timezone

from ..models import File


def touch_folders(*folder_ids):
    """Date the folders *folder_ids* now; ``None`` (the root) is skipped."""
    ids = {pk for pk in folder_ids if pk is not None}
    if not ids:
        return
    now = timezone.now()
    # A queryset update: a save() on a loaded folder would write back every
    # column of its snapshot, and the date alone is what changes.
    File.objects.filter(
        pk__in=ids, node_type=File.NodeType.FOLDER, updated_at__lt=now
    ).update(updated_at=now)
