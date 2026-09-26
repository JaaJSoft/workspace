"""Keep MediaItem rows, and the faces found in photos, in step with the files.

Registered with the file-event dispatcher, so it runs off-request (the
files.run_file_event_handlers task) once an upload or a content replacement
has committed. Registered with the hourly catch-up too (files.catch_up), which
analyzes whatever that path missed.
"""

from django.db import transaction

from workspace.files.models import FileEvent
from workspace.files.services.catch_up import register_catch_up
from workspace.files.services.event_dispatch import on_file_event

from .analysis import (
    analyze_media,
    forget_media,
    is_media_candidate,
    pending_media_qs,
    refresh_media_item,
)
from .face_analysis import (
    faces_catch_up_enabled,
    forget_faces,
    is_face_candidate,
    pending_faces_qs,
    refresh_faces,
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
        forget_faces(file_obj)
    _queue_faces(file_obj)


@on_file_event(FileEvent.Action.MOVED)
def follow_moved_photo(event):
    """A photo moved into a group folder leaves its owner's face library."""
    file_obj = event.file
    analysis = getattr(file_obj, "face_analysis", None)
    if analysis is not None and (
        file_obj.group_id is not None or analysis.owner_id != file_obj.owner_id
    ):
        forget_faces(file_obj)
    _queue_faces(file_obj)


def _queue_faces(file_obj):
    # Its own task: running the models here would hold up every other
    # handler of the event.
    if is_face_candidate(file_obj):
        from ..tasks import analyze_photo_faces

        uuid = str(file_obj.pk)
        transaction.on_commit(lambda: analyze_photo_faces.delay(uuid))


register_catch_up("photos", pending=pending_media_qs, process=refresh_media_item)
register_catch_up(
    "faces",
    pending=pending_faces_qs,
    process=refresh_faces,
    enabled=faces_catch_up_enabled,
)
