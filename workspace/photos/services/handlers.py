"""Keep MediaItem rows, and the faces found in photos and videos, in step with
the files.

Both are upload processors (files.services.processors): the upload pipeline
runs them once an upload or a content replacement has committed, and the
hourly catch-up analyzes whatever that path missed. Moves are followed by a
file-event handler of their own.
"""

from django.db import transaction
from django.db.models import F, Q

from workspace.files.models import File, FileEvent
from workspace.files.services.event_dispatch import on_file_event
from workspace.files.services.processors import register_processor

from .analysis import (
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


def _forget_photo(file_obj):
    # A photo overwritten with something else is no longer a photo.
    forget_media(file_obj)
    forget_faces(file_obj)


@on_file_event(FileEvent.Action.MOVED)
def follow_moved_photo(event):
    """A photo moved into a group folder, or given to another owner, leaves
    its owner's face library.

    A folder's move is one event for the whole subtree, so the photos under
    it are looked up here. Those that become readable are left to the hourly
    catch-up: one event must not queue a task per photo of a large folder.
    """
    moved = event.file
    if moved.node_type == File.NodeType.FOLDER:
        for photo in File.objects.filter(
            moved._descendant_filter(),
            Q(group__isnull=False) | ~Q(owner_id=F("face_analysis__owner_id")),
            face_analysis__isnull=False,
        ):
            forget_faces(photo)
        return
    analysis = getattr(moved, "face_analysis", None)
    if analysis is not None and (
        moved.group_id is not None or analysis.owner_id != moved.owner_id
    ):
        forget_faces(moved)
    _queue_faces(moved)


def _queue_faces(file_obj):
    # Its own task: running the models here would hold up every processor
    # behind it.
    if is_face_candidate(file_obj):
        from ..tasks import face_analysis_task

        task = face_analysis_task(file_obj.type)
        uuid = str(file_obj.pk)
        transaction.on_commit(lambda: task.delay(uuid))


register_processor(
    "photos",
    applies_to=is_media_candidate,
    forget=_forget_photo,
    pending=pending_media_qs,
    process=refresh_media_item,
)
register_processor(
    "faces",
    applies_to=is_face_candidate,
    enqueue=_queue_faces,
    pending=pending_faces_qs,
    process=refresh_faces,
    enabled=faces_catch_up_enabled,
)
