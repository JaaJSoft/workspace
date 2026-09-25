"""React to file lifecycle events by (re)generating image thumbnails.

Registered with the file-event dispatcher; runs off-request via the
files.run_file_event_handlers task whenever an image file is created or has
its content replaced. Registered with the hourly catch-up too
(services/catch_up.py), which generates whatever that path missed.
"""

from __future__ import annotations

from workspace.files.models import FileEvent
from workspace.files.services.catch_up import register_catch_up
from workspace.files.services.event_dispatch import on_file_event
from workspace.files.services.thumbnails.generation import (
    can_generate_thumbnail,
    pending_thumbnails_qs,
    refresh_thumbnail,
)


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def generate_thumbnail_for_event(event):
    """Generate or refresh the thumbnail for a created/updated image file."""
    file = event.file
    if file.deleted_at is not None:
        # Trashed before we ran; the backfill regenerates on restore.
        return
    if can_generate_thumbnail(file.type):
        refresh_thumbnail(file)


register_catch_up(
    "thumbnails", pending=pending_thumbnails_qs, process=refresh_thumbnail
)
