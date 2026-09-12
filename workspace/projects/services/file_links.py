"""Task-to-file links: references to live workspace files, no copy.

The row points at the File itself, so renames, edits and trashing follow
through. Each viewer only sees the links whose file they can already open;
a link never widens file access.
"""

from django.db import IntegrityError, transaction

from workspace.files.models import File
from workspace.files.services import FilePermission, FileService
from workspace.files.services.filetype import get_color, get_icon

from ..models import Task, TaskEvent, TaskFileLink
from .events import record_task_event

FILE_EVENT_TYPES = (TaskEvent.Type.FILE_LINKED, TaskEvent.Type.FILE_UNLINKED)


def link_files(user, task, files):
    """Link each of *files* to *task*, skipping those already linked.

    One FILE_LINKED event per new link, snapshotting the file name and
    uuid so the entry outlives a rename or a deletion of the file.

    The task row is locked for the duration so two concurrent calls
    serialize on the "already linked" check; the unique constraint is the
    backstop on backends where the lock does not, and a duplicate is then
    skipped rather than surfaced.
    """
    created = []
    with transaction.atomic():
        list(Task.objects.select_for_update().filter(pk=task.pk))
        linked = set(
            TaskFileLink.objects.filter(
                task=task, file_id__in=[f.pk for f in files]
            ).values_list("file_id", flat=True)
        )
        for file_obj in files:
            if file_obj.pk in linked:
                continue
            try:
                with transaction.atomic():
                    link = TaskFileLink.objects.create(
                        task=task, file=file_obj, created_by=user
                    )
            except IntegrityError:
                continue
            linked.add(file_obj.pk)
            created.append(link)
            record_task_event(
                task,
                type=TaskEvent.Type.FILE_LINKED,
                actor=user,
                to_value=file_obj.name[:100],
                to_ref=file_obj.uuid,
            )
    return created


def unlink_file(link, *, actor=None):
    """Remove *link*, leaving a FILE_UNLINKED event on the task."""
    with transaction.atomic():
        record_task_event(
            link.task,
            type=TaskEvent.Type.FILE_UNLINKED,
            actor=actor,
            to_value=link.file.name[:100],
            to_ref=link.file_id,
        )
        link.delete()


def file_links_for_task(user, task):
    """Serialize *task*'s file links for one viewer.

    One bulk permission pass; a link whose file the viewer cannot open is
    dropped entirely - its existence must not leak. A trashed file only
    reaches its owner (the permission helper's rule), flagged ``in_trash``
    so the row can say why it no longer opens.
    """
    links = list(
        task.file_links.select_related("file", "created_by").order_by(
            "created_at", "uuid"
        )
    )
    permissions = FileService.get_permissions_bulk(user, [link.file for link in links])
    items = []
    for link in links:
        perm = permissions.get(link.file_id)
        if perm is None:
            continue
        file_obj = link.file
        items.append(
            {
                "uuid": str(link.uuid),
                "file_uuid": str(file_obj.uuid),
                "name": file_obj.name,
                "type": file_obj.type,
                "type_icon": get_icon(file_obj.type),
                "type_color": get_color(file_obj.type),
                "size": file_obj.size or 0,
                "in_trash": file_obj.deleted_at is not None,
                "can_edit": perm >= FilePermission.WRITE,
                "download_url": f"/api/v1/files/{file_obj.uuid}/download",
                "added_by": link.created_by.username if link.created_by else None,
                "created_at": link.created_at.isoformat(),
            }
        )
    return items


def visible_file_refs(user, events):
    """The ``to_ref`` uuids of the file events in *events* whose file *user*
    can open right now.

    A file event names the file in its label; the name must not reach a
    viewer the file itself is hidden from, so the caller passes this set to
    the event serializer and it drops the name for every other ref. A
    hard-deleted file has no row left to check and is never visible.
    """
    refs = {ev.to_ref for ev in events if ev.type in FILE_EVENT_TYPES and ev.to_ref}
    if not refs:
        return set()
    files = list(File.objects.filter(uuid__in=refs))
    permissions = FileService.get_permissions_bulk(user, files)
    return {f.uuid for f in files if permissions.get(f.pk) is not None}
