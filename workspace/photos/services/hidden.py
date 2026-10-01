"""Hiding photos, videos and folders from one user's photo library.

What is hidden stays where it is in Files: a ``HiddenFile`` row only takes it
out of what ``queries.library_files`` returns to that user.
"""

from ..models import HiddenFile


def hide_files(user, files):
    """Hide *files* from *user*'s library; returns how many were not yet."""
    files = list(files)
    already = HiddenFile.objects.filter(owner=user, file__in=files).count()
    HiddenFile.objects.bulk_create(
        [HiddenFile(owner=user, file=file_obj) for file_obj in files],
        ignore_conflicts=True,
    )
    return len(files) - already


def unhide_files(user, file_uuids):
    """Bring the files *file_uuids* names back into *user*'s library;
    returns how many were hidden. Uuids hidden by nobody are ignored."""
    deleted, _ = HiddenFile.objects.filter(owner=user, file_id__in=file_uuids).delete()
    return deleted


def hidden_folder_data(folder):
    """What the Preferences panel shows of a hidden folder."""
    return {
        "uuid": str(folder.uuid),
        "name": folder.name,
        "path": folder.path or folder.name,
        "group": folder.group.name if folder.group_id else None,
    }
