"""Keep Photo rows in step with the content of the files they describe.

Registered with the file-event dispatcher, so it runs off-request (the
files.run_file_event_handlers task) once an upload or a content replacement
has committed. The hourly photos.analyze_pending task catches whatever a lost
dispatch left behind.
"""

from workspace.files.models import FileEvent
from workspace.files.services.event_dispatch import on_file_event

from .analysis import analyze_photo, forget_photo, is_photo_candidate


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def analyze_photo_for_event(event):
    file_obj = event.file
    if file_obj.deleted_at is not None:
        # Trashed before we ran; the catch-up reads it after a restore.
        return
    if is_photo_candidate(file_obj):
        analyze_photo(file_obj)
    elif event.action == FileEvent.Action.CONTENT_REPLACED:
        # A photo overwritten with something else is no longer a photo.
        forget_photo(file_obj)
