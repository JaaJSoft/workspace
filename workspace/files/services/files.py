"""Centralized file management service.

The heavy lifting lives in helper modules in this package
(``_storage_ops``, ``_names``, ``_content``); ``FileService`` here is a
thin facade that exposes a stable public surface to views, serializers,
sync, and tasks.
"""

import enum
import logging

from django.db import transaction

from ..metrics import FILES_UPLOAD_BYTES
from ..models import File, FileEvent
from . import _content as _content_helpers
from . import _names as _name_helpers
from . import _storage_ops as _storage
from .events import record_event


class FilePermission(enum.IntEnum):
    """Permission levels for file access, ordered by capability."""

    VIEW = 10  # share-ro: read/download only
    WRITE = 20  # share-rw: update content only
    EDIT = 30  # group member: full CRUD
    MANAGE = 40  # owner: full control + share management


logger = logging.getLogger(__name__)


class FileService:
    """Stateless service that encapsulates file/folder business logic."""

    # ------------------------------------------------------------------
    # Querysets
    # ------------------------------------------------------------------

    @staticmethod
    def _access_branches(user):
        """Return the per-branch filter kwargs for *user*'s accessible files.

        Single source of truth for the three access branches (owned, group,
        shared), consumed by both ``accessible_files_q`` (ORed into one Q
        across a join) and ``accessible_file_ids`` (each branch a separately
        indexed UNION arm). Defining the branches once keeps the two
        permission paths from drifting - a divergence would mean a leak or a
        hole in one of them.
        """
        return (
            {"owner": user},
            {"group__in": user.groups.all()},
            {"shares__shared_with": user},
        )

    @staticmethod
    def accessible_files_q(user):
        """Return a Q filter matching all files *user* can access."""
        from django.db.models import Q

        # Seed from the first branch rather than an empty Q(): an empty Q in
        # an OR chain can match everything - a full access leak here.
        branches = FileService._access_branches(user)
        q = Q(**branches[0])
        for branch in branches[1:]:
            q |= Q(**branch)
        return q

    @staticmethod
    def accessible_file_ids(user):
        """Return a values queryset of ids of all files *user* can access.

        Same semantics as ``accessible_files_q`` (owned + group + shared,
        ``deleted_at`` intentionally not filtered), but built as a UNION of
        three independently indexed queries. The Q form ORs across a join
        (``shares__shared_with``), which defeats per-branch index use and
        forces a full scan of the files table - O(total files) on every
        call. The UNION stays proportional to what the user can actually
        see, so hot paths (activity feed) should prefer this helper as a
        ``pk__in=`` / ``file_id__in=`` source.

        UNION querysets are terminal: no further ``filter()``/``annotate()``
        is possible. When the access filter must compose with other
        conditions in the same query, keep using ``accessible_files_q``.
        The empty ``order_by()`` on each branch is required - ``File`` has a
        default ``Meta.ordering`` and ORDER BY is invalid inside a compound
        subquery.
        """
        arms = [
            File.objects.filter(**branch).order_by().values_list("pk", flat=True)
            for branch in FileService._access_branches(user)
        ]
        return arms[0].union(*arms[1:])

    @staticmethod
    def user_files_qs(user):
        """Return a queryset of active (non-deleted) personal files owned by the user."""
        return File.objects.filter(
            owner=user, group__isnull=True, deleted_at__isnull=True
        )

    @staticmethod
    def user_group_files_qs(user):
        """Return a queryset of active group files the user can access."""
        return File.objects.filter(
            group__in=user.groups.all(),
            deleted_at__isnull=True,
        )

    @staticmethod
    def annotate_for_serializer(queryset, user):
        """Prepare a File queryset for ``FileSerializer``."""
        from django.db.models import Exists, OuterRef

        from workspace.files.models import FileFavorite, FileShare, PinnedFolder

        return queryset.annotate(
            is_favorite=Exists(
                FileFavorite.objects.filter(owner=user, file_id=OuterRef("pk"))
            ),
            is_pinned=Exists(
                PinnedFolder.objects.filter(owner=user, folder_id=OuterRef("pk"))
            ),
            is_shared=Exists(FileShare.objects.filter(file_id=OuterRef("pk"))),
            has_children=Exists(
                File.objects.filter(
                    parent_id=OuterRef("pk"),
                    node_type=File.NodeType.FOLDER,
                    deleted_at__isnull=True,
                )
            ),
        ).prefetch_related("file_tags__tag")

    @staticmethod
    def storage_used(user):
        """Return total bytes used by *user*'s non-deleted files."""
        from django.db.models import Sum

        total = File.objects.filter(
            owner=user,
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        ).aggregate(total=Sum("size"))["total"]
        return total or 0

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @staticmethod
    def create_file(
        owner,
        name,
        parent=None,
        *,
        content=None,
        mime_type=None,
        group=None,
        acting_user=None,
    ):
        """Create a new file record, optionally with uploaded content."""
        from workspace.files.services.detection import (
            detect_from_name,
            detect_from_stream,
            refine_with_name,
        )

        if group is None and parent and parent.group_id:
            group = parent.group

        if content is not None:
            detection = detect_from_stream(content)
        else:
            detection = detect_from_name(name)

        # Magika reads a sparse ".md" as txt; honour the extension for the
        # stored label so notes stay discoverable (the notes browser and the
        # [[ search both filter on type=markdown).
        label = refine_with_name(detection.label, name)

        if not mime_type:
            mime_type = detection.mime_type

        size = content.size if content is not None else None

        file_obj = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            parent=parent,
            mime_type=mime_type or "application/octet-stream",
            type=label,
            category=detection.group or "unknown",
            size=size,
            group=group,
        )
        if content is not None:
            file_obj.content = content
        file_obj.save()
        if size:
            FILES_UPLOAD_BYTES.inc(size)
        record_event(file_obj, acting_user or owner, FileEvent.Action.CREATED)
        return file_obj

    @staticmethod
    def create_folder(
        owner, name, parent=None, *, icon=None, color=None, group=None, acting_user=None
    ):
        """Create a new folder record."""
        if group is None and parent and parent.group_id:
            group = parent.group

        folder = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FOLDER,
            parent=parent,
            icon=icon,
            color=color,
            group=group,
        )
        folder.save()
        FileService._ensure_folder_on_storage(folder)
        record_event(folder, acting_user or owner, FileEvent.Action.CREATED)
        return folder

    @staticmethod
    def register_disk_file(
        owner,
        name,
        parent,
        content_path,
        *,
        mime_type=None,
        size=None,
        acting_user=None,
    ):
        """Register a file that already exists on disk (used by sync)."""
        from workspace.files.services.detection import detect_from_name

        detection = detect_from_name(name)
        if not mime_type:
            mime_type = detection.mime_type

        file_obj = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            parent=parent,
            mime_type=mime_type,
            type=detection.label,
            category=detection.group or "unknown",
            size=size,
        )
        file_obj.content.name = content_path
        file_obj.save()
        record_event(file_obj, acting_user, FileEvent.Action.CREATED)
        return file_obj

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def move(file_obj, new_parent, *, acting_user=None):
        """Move a file or folder to a new parent, handling physical storage."""
        old_parent_id = file_obj.parent_id
        new_parent_id = new_parent.pk if new_parent else None
        if old_parent_id == new_parent_id:
            return

        new_group = new_parent.group if new_parent else None
        old_group = file_obj.group

        # Determine new owner for storage path computation
        new_owner = (
            acting_user if (old_group and not new_group and acting_user) else None
        )

        if file_obj.node_type == File.NodeType.FOLDER:
            FileService._move_folder_storage(file_obj, new_parent, new_owner=new_owner)
        else:
            if file_obj.content and file_obj.content.name:
                FileService._move_file_storage(
                    file_obj, new_parent, new_owner=new_owner
                )

        # Update parent in DB before propagating group to avoid unique constraint
        # violations (e.g. unique_group_root_folder).
        file_obj.parent = new_parent
        file_obj.save()

        # Propagate group change
        if (old_group and old_group != new_group) or (not old_group and new_group):
            FileService.propagate_group(file_obj, new_group)

        # Update owner when moving from group to personal
        if old_group and not new_group and new_owner:
            File.objects.filter(file_obj._descendant_filter()).update(owner=new_owner)
            file_obj.owner = new_owner

        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.MOVED,
            {
                "old_parent_id": str(old_parent_id) if old_parent_id else None,
                "new_parent_id": str(new_parent_id) if new_parent_id else None,
            },
        )

    @staticmethod
    def propagate_group(file_obj, group):
        """Set group on file_obj and all its descendants."""
        File.objects.filter(file_obj._descendant_filter()).update(group=group)
        file_obj.group = group

    @staticmethod
    @transaction.atomic
    def rename(file_obj, new_name, *, acting_user=None):
        """Rename a file or folder, moving physical storage files as needed."""
        old_name = file_obj.name
        if old_name == new_name:
            return file_obj

        if file_obj.node_type == File.NodeType.FOLDER:
            FileService._rename_folder_storage(file_obj, old_name, new_name)
        else:
            if file_obj.content and file_obj.content.name:
                FileService._rename_file_storage(file_obj, new_name)

        file_obj.name = new_name
        file_obj.save()
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.RENAMED,
            {
                "old_name": old_name,
                "new_name": new_name,
            },
        )
        return file_obj

    @staticmethod
    def update_content(
        file_obj, content, *, name=None, mime_type=None, acting_user=None
    ):
        """Replace a file's content, updating size and MIME type."""
        from workspace.files.services.detection import (
            detect_from_stream,
            refine_with_name,
        )

        detection = detect_from_stream(content)
        file_obj.size = content.size
        file_obj.mime_type = mime_type or detection.mime_type
        # Honour the extension when Magika's content label is generic (a sparse
        # ".md" reads as txt) so an edited note keeps type=markdown.
        file_obj.type = refine_with_name(detection.label, name or file_obj.name)
        file_obj.category = detection.group or "unknown"
        file_obj.has_thumbnail = False
        file_obj.content = content
        file_obj.save()
        if file_obj.size:
            FILES_UPLOAD_BYTES.inc(file_obj.size)
        record_event(file_obj, acting_user, FileEvent.Action.CONTENT_REPLACED)
        return file_obj

    @staticmethod
    def replace_content_storage(file_obj, *, storage_path, size, acting_user=None):
        """Point *file_obj* at content already written to *storage_path*."""
        from workspace.files.services.detection import detect_from_name

        detection = detect_from_name(file_obj.name)
        file_obj.size = size
        file_obj.mime_type = detection.mime_type
        file_obj.type = detection.label
        file_obj.category = detection.group or "unknown"
        file_obj.has_thumbnail = False
        file_obj.content.name = storage_path
        file_obj.save()
        if size:
            FILES_UPLOAD_BYTES.inc(size)
        record_event(file_obj, acting_user, FileEvent.Action.CONTENT_REPLACED)
        return file_obj

    @staticmethod
    def copy(file_obj, target_parent, owner, *, acting_user=None):
        """Recursively copy a file or folder to *target_parent*."""
        copied = _storage.copy_node(file_obj, target_parent, owner)
        record_event(
            copied,
            acting_user or owner,
            FileEvent.Action.CREATED,
            {
                "source_uuid": str(file_obj.uuid),
                "source_name": file_obj.name,
            },
        )
        return copied

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def soft_delete(file_obj, *, acting_user=None):
        """Soft-delete a file or folder (cascades to descendants)."""
        count = file_obj.soft_delete()
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.DELETED,
            {
                "cascade_count": count,
            },
        )
        return count

    @staticmethod
    def restore(file_obj, *, acting_user=None):
        """Restore a soft-deleted file or folder from trash."""
        count = file_obj.restore()
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.RESTORED,
            {
                "cascade_count": count,
            },
        )
        return count

    @staticmethod
    def hard_delete(file_obj, *, acting_user=None):
        """Permanently delete a file or folder. No event - the row vanishes."""
        # The cascade also wipes any FileEvent rows on this file.
        file_obj.delete(hard=True)

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    @staticmethod
    def get_permission(user, file_obj):
        """Return the access permission level for *user* on *file_obj*."""
        from workspace.files.models import FileShare

        if file_obj.owner_id == user.id:
            return FilePermission.MANAGE
        if file_obj.deleted_at is not None:
            return None
        if file_obj.group_id and user.groups.filter(id=file_obj.group_id).exists():
            return FilePermission.EDIT
        share = (
            FileShare.objects.filter(
                file=file_obj,
                shared_with=user,
            )
            .values_list("permission", flat=True)
            .first()
        )
        if share is None:
            return None
        return FilePermission.WRITE if share == "rw" else FilePermission.VIEW

    @staticmethod
    def can_access(user, file_obj):
        """Check whether *user* can access *file_obj*."""
        return FileService.get_permission(user, file_obj) is not None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def check_name_available(owner, parent, name, node_type, *, exclude_pk=None):
        """Raise ``ValueError`` if a file with the same name already exists."""
        return _name_helpers.check_name_available(
            owner,
            parent,
            name,
            node_type,
            exclude_pk=exclude_pk,
        )

    @staticmethod
    def validate_move_target(file_obj, new_parent, user=None):
        """Raise ``ValueError`` if *new_parent* is an invalid move target."""
        return _name_helpers.validate_move_target(file_obj, new_parent, user=user)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    _IMAGE_MIME_PREFIXES = (
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    )

    @staticmethod
    def read_text_content(file_obj, *, max_bytes=32_768):
        """Read and return the text content of a file."""
        return _content_helpers.read_text_content(file_obj, max_bytes=max_bytes)

    @staticmethod
    def read_image_content(file_obj, *, max_bytes=10_485_760):
        """Read and return the raw bytes of an image file."""
        return _content_helpers.read_image_content(file_obj, max_bytes=max_bytes)

    # ------------------------------------------------------------------
    # Internal helpers (kept on the class so existing test patches at
    # ``workspace.files.services.files.FileService._<helper>`` keep working)
    # ------------------------------------------------------------------

    @staticmethod
    def _rename_file_storage(file_obj, new_name):
        return _storage.rename_file_storage(file_obj, new_name)

    @staticmethod
    def _rename_folder_storage(folder, old_folder_name, new_folder_name):
        return _storage.rename_folder_storage(folder, old_folder_name, new_folder_name)

    @staticmethod
    def _folder_storage_path(folder):
        return _storage.folder_storage_path(folder)

    @staticmethod
    def _update_descendant_content_names(folder, old_seg, new_seg):
        return _storage.update_descendant_content_names(folder, old_seg, new_seg)

    @staticmethod
    def _ensure_folder_on_storage(folder):
        return _storage.ensure_folder_on_storage(folder)

    @staticmethod
    def _copy_node(node, parent, owner, _sibling_names=None):
        return _storage.copy_node(node, parent, owner, _sibling_names)

    @staticmethod
    def _parent_storage_path(owner, parent):
        return _storage.parent_storage_path(owner, parent)

    @staticmethod
    def _move_folder_storage(folder, new_parent, *, new_owner=None):
        return _storage.move_folder_storage(folder, new_parent, new_owner=new_owner)

    @staticmethod
    def _move_file_storage(file_obj, new_parent, *, new_owner=None):
        return _storage.move_file_storage(file_obj, new_parent, new_owner=new_owner)

    @staticmethod
    def _unique_copy_name(base_name, node_type, existing_names):
        return _storage.unique_copy_name(base_name, node_type, existing_names)
