"""React to file lifecycle events by (re)generating image thumbnails.

Registered with the file-event dispatcher; runs off-request via the
files.run_file_event_handlers task whenever an image file is created or has
its content replaced. The periodic generate_thumbnails task remains as a
backfill for pre-existing files and any missed dispatch.
"""

from __future__ import annotations

from workspace.files.models import FileEvent
from workspace.files.services.event_dispatch import on_file_event
from workspace.files.services.thumbnails import (
    can_generate_thumbnail,
    generate_thumbnail,
)


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def generate_thumbnail_for_event(event):
    """Generate or refresh the thumbnail for a created/updated image file."""
    file = event.file
    if file.deleted_at is not None:
        # Trashed before we ran; the backfill regenerates on restore.
        return
    if not can_generate_thumbnail(file.type):
        return
    if generate_thumbnail(file):
        file.has_thumbnail = True
        file.save(update_fields=["has_thumbnail"])
