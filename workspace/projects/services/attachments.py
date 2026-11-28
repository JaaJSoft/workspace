"""Task attachments: files stored on the task itself.

The blob belongs to the task - anyone who can open the task sees every
attachment. Attaching a workspace file copies its content, so the source
file's own lifecycle (trash, deletion, permission changes) never reaches
the task copy.
"""

import logging

from django.core.files.base import File as DjangoFile
from django.core.files.storage import default_storage
from django.db import transaction

from workspace.common.logging import scrub
from workspace.files.services.detection import detect_from_stream
from workspace.files.services.filetype import pin_viewer_for_upload

from ..models import TaskAttachment, TaskEvent
from .events import record_task_event

logger = logging.getLogger(__name__)

MAX_ATTACHMENTS_PER_REQUEST = 10
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def create_attachments(user, task, uploads, workspace_files):
    """Store *uploads* and copies of *workspace_files* on *task*.

    Workspace files are streamed into a fresh blob owned by the task.
    Raises ``OSError`` (incl. ``FileNotFoundError``) when a workspace
    file's content is unavailable; callers map that to a 4xx. One
    activity event covers the whole batch.

    All or nothing: a failure takes back the rows already written, and the
    blobs with them. Storage does not roll back, so they are dropped by hand
    once the rows are gone.
    """
    created = []
    try:
        with transaction.atomic():
            for f in uploads:
                detection = detect_from_stream(f, f.name)
                created.append(
                    TaskAttachment.objects.create(
                        task=task,
                        file=f,
                        original_name=f.name,
                        mime_type=detection.mime_type,
                        type=detection.label,
                        category=detection.group or "unknown",
                        viewer=pin_viewer_for_upload(detection.label, f.content_type),
                        size=f.size,
                        added_by=user,
                    )
                )
            for ws_file in workspace_files:
                attachment = TaskAttachment(
                    task=task,
                    original_name=ws_file.name,
                    mime_type=ws_file.mime_type or "application/octet-stream",
                    type=ws_file.type or "unknown",
                    category=ws_file.category or "unknown",
                    viewer=ws_file.viewer,
                    size=ws_file.size or 0,
                    added_by=user,
                )
                with ws_file.content.open("rb") as fh:
                    attachment.file = DjangoFile(fh, name=ws_file.name)
                    attachment.save()
                created.append(attachment)
            if created:
                record_task_event(task, type=TaskEvent.Type.ATTACHED, actor=user)
    except BaseException:
        for attachment in created:
            _delete_blob(attachment.file.name)
        raise
    return created


def remove_attachment(attachment, actor):
    """Delete *attachment* and its blob, recording the activity event."""
    task = attachment.task
    attachment.delete()
    record_task_event(task, type=TaskEvent.Type.DETACHED, actor=actor)


def discard_blob_on_delete(sender, instance, using, **kwargs):
    """``post_delete`` receiver: drop the blob of a deleted attachment.

    A signal rather than a step of each delete, because the row also goes
    with its task, its project or its uploader's account, through cascades
    no service sees. Storage does not roll back: the blob only goes once
    the deletion commits.
    """
    name = instance.file.name
    if name:
        transaction.on_commit(lambda: _delete_blob(name), using=using)


def _delete_blob(name):
    try:
        default_storage.delete(name)
    except OSError:
        logger.warning("Could not delete task attachment %s", scrub(name))
