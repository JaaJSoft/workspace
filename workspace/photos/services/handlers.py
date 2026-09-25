"""Keep MediaItem rows in step with the content of the files they describe.

Registered with the file-event dispatcher, so it runs off-request (the
files.run_file_event_handlers task) once an upload or a content replacement
has committed. Registered with the hourly catch-up too (files.catch_up), which
analyzes whatever that path missed.
"""

from workspace.files.models import FileEvent
from workspace.files.services.catch_up import register_catch_up
from workspace.files.services.event_dispatch import on_file_event

from .analysis import (
    analyze_media,
    forget_media,
    is_media_candidate,
    pending_media_qs,
)


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def analyze_media_for_event(event):
    file_obj = event.file
    if file_obj.deleted_at is not None:
        # Trashed before we ran; the catch-up reads it after a restore.
        return
    if is_media_candidate(file_obj):
        analyze_media(file_obj)
    elif event.action == FileEvent.Action.CONTENT_REPLACED:
        # A photo overwritten with something else is no longer a photo.
        forget_media(file_obj)


register_catch_up("photos", pending=pending_media_qs, process=analyze_media)
