"""Where a photo was taken stays with its owner, unless they say otherwise.

With the owner's ``photos`` / ``strip_location_on_share`` preference on (the
default), anyone else gets their photos and videos without the position:
someone they shared a file, a folder or an album with, a visitor through a
public link. The stored original is never touched, and the owner keeps the
position in their own library.

A group folder is a space its members share on purpose: they get the
original. Someone outside the group, through a share or a link, does not.

The bytes are patched as they are served (``gps_redaction``), through the
filter ``files`` runs on what it serves to someone else
(``files.content_filters``); the properties panel hides the coordinates and
the map the same way (``details.section_context``).
"""

from django.contrib.auth import get_user_model

from .gps_redaction import PatchedFile, location_patches
from .preferences import strip_location_on_share


def withholds_location(file_obj, reader):
    """Whether *reader* (None for a visitor through a link) must not learn
    where *file_obj* was taken."""
    if reader is not None and reader.is_authenticated:
        if reader.pk == file_obj.owner_id:
            return False
        if file_obj.group_id and reader.groups.filter(pk=file_obj.group_id).exists():
            return False
    # The preference is read by the owner's id: a zip of a hundred photos
    # costs one cached read, not a hundred owner rows.
    return strip_location_on_share(get_user_model()(pk=file_obj.owner_id))


def strip_location(file_obj, reader, handle):
    """The content filter: *handle* with the position blanked, or None when
    *reader* may read it as is or there is no position to blank."""
    if not withholds_location(file_obj, reader):
        return None
    patches = location_patches(handle)
    if not patches:
        return None
    return PatchedFile(handle, patches)
