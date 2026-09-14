"""Task-to-file links: a workspace file shared with the project and pinned
on a task.

Linking a file creates (or reuses) the ``FileShare`` addressing the task's
project and records which task it hangs on. The link points at the share,
so revoking the share or deleting the file takes the link with it, and
every project member sees every linked file by construction. Nothing is
copied: renames and edits follow through.
"""

from django.db import IntegrityError, transaction

from workspace.files.actions import ActionRegistry
from workspace.files.models import FileShare
from workspace.files.services import FileService
from workspace.files.services.filetype import get_color, get_icon
from workspace.files.services.sharing import share_file, unshare_file

from ..models import Task, TaskEvent, TaskFileLink
from .events import record_task_event
from .members import ProjectRuleError

_CANNOT_SHARE_DETAIL = "One or more files cannot be shared with the project."


def link_files(user, task, files, *, permission=FileShare.Permission.READ_ONLY):
    """Share each of *files* with *task*'s project and pin it on *task*.

    *user* must be allowed to share every file (the files ``share`` action
    decides), or ProjectRuleError is raised and nothing is written. A file
    already shared with the project keeps its share and permission; a file
    already linked to the task is skipped. One FILE_LINKED event per new
    link, snapshotting the file name and uuid so the entry outlives the
    file.

    The task row is locked for the duration so two concurrent calls
    serialize on the "already linked" check; the unique constraint is the
    backstop on backends where the lock does not, and a duplicate is then
    skipped rather than surfaced.
    """
    permissions = FileService.get_permissions_bulk(user, files)
    for file_obj in files:
        if not ActionRegistry.is_action_available(
            "share", user, file_obj, permission=permissions.get(file_obj.pk)
        ):
            raise ProjectRuleError(_CANNOT_SHARE_DETAIL)
    created = []
    with transaction.atomic():
        list(Task.objects.select_for_update().filter(pk=task.pk))
        project = task.project
        linked = set(
            TaskFileLink.objects.filter(task=task).values_list(
                "share__file_id", flat=True
            )
        )
        for file_obj in files:
            if file_obj.pk in linked:
                continue
            share = FileShare.objects.filter(
                file=file_obj, shared_with_project=project
            ).first()
            if share is None:
                share, _, _ = share_file(
                    file_obj,
                    target_project=project,
                    permission=permission,
                    acting_user=user,
                )
            try:
                with transaction.atomic():
                    link = TaskFileLink.objects.create(
                        task=task, share=share, created_by=user
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
    """Remove *link*, leaving a FILE_UNLINKED event on the task.

    The project share goes with it when no other task in the project still
    links the file: the share only ever existed for the links.
    """
    with transaction.atomic():
        share = link.share
        file_obj = share.file
        record_task_event(
            link.task,
            type=TaskEvent.Type.FILE_UNLINKED,
            actor=actor,
            to_value=file_obj.name[:100],
            to_ref=file_obj.uuid,
        )
        link.delete()
        if not TaskFileLink.objects.filter(share=share).exists():
            unshare_file(
                file_obj, target_project=share.shared_with_project, acting_user=actor
            )


def file_links_for_task(task):
    """Serialize *task*'s file links for the panel and the API.

    No per-viewer filtering: the share behind each link addresses the
    project, so every member can open the file. A trashed file stays
    listed, flagged ``in_trash`` so the row can say why it no longer opens.
    """
    links = task.file_links.select_related("share__file", "created_by").order_by(
        "created_at", "uuid"
    )
    items = []
    for link in links:
        file_obj = link.share.file
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
                "permission": link.share.permission,
                "download_url": f"/api/v1/files/{file_obj.uuid}/download",
                "added_by": link.created_by.username if link.created_by else None,
                "created_at": link.created_at.isoformat(),
            }
        )
    return items
