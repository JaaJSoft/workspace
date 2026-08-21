"""Task attachments: link rows between tasks and workspace files."""

from workspace.files.models import File
from workspace.files.services import FileService

from ..models import TaskAttachment, TaskEvent
from .events import record_task_event

UPLOADS_FOLDER_NAME = "Task attachments"
MAX_ATTACHMENTS_PER_REQUEST = 10
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def visible_attachments(user, task):
    """Attachments whose file still lives and *user* can access.

    The link never widens file access: a project member without permission
    on the underlying file simply does not see the attachment.
    """
    links = list(
        task.attachments.select_related("file", "added_by").filter(
            file__deleted_at__isnull=True
        )
    )
    permissions = FileService.get_permissions_bulk(user, [link.file for link in links])
    return [link for link in links if permissions[link.file.pk] is not None]


def uploads_folder(user):
    """The uploader's "Task attachments" folder, created on first use."""
    folder = File.objects.filter(
        owner=user,
        parent__isnull=True,
        group__isnull=True,
        node_type=File.NodeType.FOLDER,
        name=UPLOADS_FOLDER_NAME,
        deleted_at__isnull=True,
    ).first()
    return folder or FileService.create_folder(user, UPLOADS_FOLDER_NAME)


def attach_files(user, task, files):
    """Link *files* (``File`` rows) to *task*; returns the new links.

    Idempotent per (task, file): re-linking an already attached file is a
    no-op, and one activity event covers the whole batch.
    """
    created = []
    for file_obj in files:
        link, was_created = TaskAttachment.objects.get_or_create(
            task=task,
            file=file_obj,
            defaults={"added_by": user},
        )
        if was_created:
            created.append(link)
    if created:
        record_task_event(task, type=TaskEvent.Type.ATTACHED, actor=user)
    return created
