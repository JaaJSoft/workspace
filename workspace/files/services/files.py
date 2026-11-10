"""Centralized file management service.

The heavy lifting lives in helper modules in this package
(``_storage_ops``, ``_names``, ``_content``); ``FileService`` here is a
thin facade that exposes a stable public surface to views, serializers,
sync, and tasks.
"""

import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext

from workspace.common.logging import scrub

from ..metrics import FILES_UPLOAD_BYTES
from ..models import File, FileEvent, canonical_name
from ..queries import can_create_in
from . import content_reading as _content_helpers
from . import names as _name_helpers
from . import storage_ops as _storage
from .events import record_event
from .hard_delete import hard_delete_tree
from .quota import check_write_allowed, subtree_bytes
from .scanning.policy import check_copy_allowed


class GroupRootFolder(ValueError):
    """A group's root folder was asked to leave its place.

    The root is the group's whole tree: moving it re-owns the subtree to the
    mover and drops the group, trashing it takes every member's files with it.
    """


class GroupMismatch(ValueError):
    """A node was asked to join a group other than its parent folder's."""

    def __init__(self, message="The group must be the parent folder's group."):
        super().__init__(message)


logger = logging.getLogger(__name__)


def new_owner_on_move(file_obj, new_parent, acting_user):
    """Who a move under *new_parent* hands *file_obj* to, or None.

    A node leaving a group folder becomes the personal node of whoever moved
    it; any other move leaves its owner alone.
    """
    leaves_group = file_obj.group_id and not (
        new_parent is not None and new_parent.group_id
    )
    return acting_user if (leaves_group and acting_user) else None


class FileService:
    """Stateless service that encapsulates file/folder business logic."""

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
        viewer=None,
        acting_user=None,
        check_quota=True,
        keep_both=False,
    ):
        """Create a new file record, optionally with uploaded content.

        ``viewer`` pins a viewer slug explicitly; None derives it from the
        declared media type. Callers copying an existing row pass the source's
        pin, which the declared type can no longer reconstruct (a voice message
        is stored as ``video/webm``).

        ``check_quota=False`` is for a caller that has already charged these
        exact bytes to the bucket itself - the archive extraction loop, which
        would otherwise re-aggregate the bucket once per entry. Every other
        caller leaves it on: this is where a write is refused.

        A taken name raises ``NameTaken``, unless ``keep_both`` asks for the
        first free ``name (Copy N)`` instead - the returned row says which.
        """
        from workspace.files.services.content_hash import upload_hash
        from workspace.files.services.detection import (
            detect_from_name,
            detect_from_stream,
            refine_with_name,
        )
        from workspace.files.services.filetype import pin_viewer_for_upload

        name = canonical_name(name)
        group = FileService.node_group(parent, group)

        # Before the detection and hashing passes read the stream. Not the
        # guarantee - the INSERT is - but it spares the loser of a known
        # collision both passes.
        if not keep_both:
            _name_helpers.check_name_available(owner, parent, name, File.NodeType.FILE)
        if check_quota:
            check_write_allowed(
                owner=owner,
                group=group,
                additional_bytes=content.size if content is not None else 0,
            )

        if content is not None:
            detection = detect_from_stream(content, name)
            content_hash = upload_hash(content)
        else:
            detection = detect_from_name(name)
            content_hash = ""

        # Magika reads a sparse ".md" as txt; honour the extension for the
        # stored label so notes stay discoverable (the notes browser and the
        # [[ search both filter on type=markdown).
        label = refine_with_name(detection.label, name)

        # The declared media type is the only signal that can tell an
        # audio-only container from a video one; acting on it is a display
        # decision, so it pins a viewer instead of rewriting the detection.
        if viewer is None:
            declared_mime = mime_type or getattr(content, "content_type", None)
            viewer = pin_viewer_for_upload(label, declared_mime)

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
            viewer=viewer,
            size=size,
            content_hash=content_hash,
            group=group,
            processing_status=(
                File.ProcessingStatus.PENDING
                if content is not None
                else File.ProcessingStatus.READY
            ),
        )
        if keep_both:
            _name_helpers.insert_node_keeping_both(file_obj, content)
        else:
            _name_helpers.insert_node(file_obj, content)
        if size:
            FILES_UPLOAD_BYTES.inc(size)
        record_event(file_obj, acting_user or owner, FileEvent.Action.CREATED)
        return file_obj

    @staticmethod
    def create_folder(
        owner, name, parent=None, *, icon=None, color=None, group=None, acting_user=None
    ):
        """Create a new folder record and its directory, or neither."""
        name = canonical_name(name)
        group = FileService.node_group(parent, group)

        _name_helpers.check_name_available(owner, parent, name, File.NodeType.FOLDER)

        folder = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FOLDER,
            parent=parent,
            icon=icon,
            color=color,
            group=group,
        )
        with transaction.atomic():
            _name_helpers.insert_node(folder)
            _storage.ensure_folder_on_storage(folder)
        record_event(folder, acting_user or owner, FileEvent.Action.CREATED)
        return folder

    @staticmethod
    def node_group(parent, group):
        """The group a node created under *parent* belongs to.

        Under a parent it is the parent's, whatever the caller asked for;
        asking for another one raises ``GroupMismatch``. At the root it is
        *group*, which the caller has checked membership of.
        """
        if parent is None:
            return group
        if group is not None and getattr(group, "pk", group) != parent.group_id:
            raise GroupMismatch()
        return parent.group

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
        """Register a file that already exists on disk (used by sync).

        *content_path* names the blob as it is stored, which the caller
        has put under the composed *name* first.
        """
        from workspace.files.services.content_hash import hash_storage_file
        from workspace.files.services.detection import detect_from_name

        name = canonical_name(name)
        detection = detect_from_name(name)
        if not mime_type:
            mime_type = detection.mime_type

        _name_helpers.check_name_available(owner, parent, name, File.NodeType.FILE)

        file_obj = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            parent=parent,
            mime_type=mime_type,
            type=detection.label,
            category=detection.group or "unknown",
            size=size,
            processing_status=File.ProcessingStatus.PENDING,
        )
        file_obj.content.name = content_path
        try:
            file_obj.content_hash = hash_storage_file(
                file_obj.content.storage, content_path
            )
        except OSError as e:
            # The row is still worth registering; the backfill command
            # picks the hash up later.
            logger.warning(
                "Cannot hash disk file %s: %s", scrub(content_path), scrub(e)
            )
        _name_helpers.insert_node(file_obj)
        record_event(file_obj, acting_user, FileEvent.Action.CREATED)
        return file_obj

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @staticmethod
    def check_move_allowed(file_obj, new_parent, *, acting_user=None):
        """Raise ``QuotaExceeded`` when moving *file_obj* under *new_parent* would not fit."""
        new_group = new_parent.group if new_parent else None
        old_group = file_obj.group
        new_owner = new_owner_on_move(file_obj, new_parent, acting_user)

        # Only a group change moves bytes between buckets. The owner charged
        # is the one the move will assign, not necessarily the current one.
        if (old_group.pk if old_group else None) != (
            new_group.pk if new_group else None
        ):
            check_write_allowed(
                owner=new_owner or file_obj.owner_id,
                group=new_group,
                # Trashed descendants travel with the subtree.
                additional_bytes=subtree_bytes(file_obj, include_trashed=True),
            )

    @staticmethod
    @transaction.atomic
    def move(file_obj, new_parent, *, acting_user=None):
        """Move a file or folder to a new parent, handling physical storage."""
        old_parent_id = file_obj.parent_id
        new_parent_id = new_parent.pk if new_parent else None
        if old_parent_id == new_parent_id:
            return
        if FileService.is_group_root(file_obj):
            raise GroupRootFolder(gettext("A group's root folder cannot be moved."))

        new_group = new_parent.group if new_parent else None
        old_group = file_obj.group

        # The destination namespace is the new owner's, not the current one's.
        new_owner = new_owner_on_move(file_obj, new_parent, acting_user)

        _name_helpers.check_name_available(
            new_owner or file_obj.owner,
            new_parent,
            file_obj.name,
            file_obj.node_type,
            exclude_pk=file_obj.pk,
        )
        FileService.check_move_allowed(file_obj, new_parent, acting_user=acting_user)

        # Rows first, bytes last: the rows are what reserve the name, and a
        # refusal must find the bytes where they were. Only the columns this
        # move changes - a bare save() would write back a content write or a
        # lock that landed meanwhile.
        source = _storage.current_node_path(file_obj)
        with _name_helpers.name_claim(
            new_owner or file_obj.owner, new_parent, file_obj.name
        ):
            file_obj.parent = new_parent
            file_obj.save(update_fields=["parent"])
            if new_owner:
                # Before the group change: the subtree is found through the
                # group, and its rows belong to every member who added one.
                # The old owner's events become foreign to the new one: an
                # overestimate of last_foreign_event_at is safe, a stale one
                # hides them.
                File.objects.filter(file_obj._descendant_filter()).update(
                    owner=new_owner, last_foreign_event_at=F("last_event_at")
                )
                file_obj.owner = new_owner
            # The group change is the statement that enters a group folder's
            # namespace, so it belongs inside the claim too.
            if (old_group and old_group != new_group) or (not old_group and new_group):
                FileService.propagate_group(file_obj, new_group)
        if source:
            _storage.move_node_storage(
                file_obj, source, _storage.node_storage_path(file_obj)
            )

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
        new_name = canonical_name(new_name)
        if old_name == new_name:
            return file_obj
        if FileService.is_group_root(file_obj):
            raise GroupRootFolder(gettext("A group's root folder cannot be renamed."))

        _name_helpers.check_name_available(
            file_obj.owner,
            file_obj.parent,
            new_name,
            file_obj.node_type,
            exclude_pk=file_obj.pk,
        )

        # Rows first, bytes last, as in ``move``. The name and the path it
        # derives, and nothing else.
        source = _storage.current_node_path(file_obj)
        file_obj.name = new_name
        with _name_helpers.name_claim(file_obj.owner, file_obj.parent, new_name):
            file_obj.save(update_fields=["name"])
        if source:
            _storage.move_node_storage(
                file_obj, source, _storage.node_storage_path(file_obj)
            )
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
    def copy(
        file_obj, target_parent, owner, *, name=None, acting_user=None, checkpoint=None
    ):
        """Recursively copy a file or folder to *target_parent*.

        The copy is named *name*, *file_obj*'s own name by default, or the next
        free variant of it when the destination already holds it. Blocked
        descendants of a folder are left behind; a blocked file raises
        ``QuarantinedSource``. *checkpoint* is called with each node once it is
        copied and may raise to abandon the copy, which then leaves nothing
        behind.
        """
        check_copy_allowed(file_obj)
        check_write_allowed(
            owner=owner,
            group=target_parent.group if target_parent is not None else None,
            # copy_node only duplicates live descendants.
            additional_bytes=subtree_bytes(file_obj, include_trashed=False),
        )
        copied = _storage.copy_node(
            file_obj, target_parent, owner, name=name, checkpoint=checkpoint
        )
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
    def soft_delete(file_obj, *, acting_user=None, deleted_at=None, details=None):
        """Soft-delete a file or folder (cascades to descendants).

        *deleted_at* defaults to now; *details* go into the recorded event.
        """
        if FileService.is_group_root(file_obj):
            raise GroupRootFolder(gettext("A group's root folder cannot be deleted."))
        return FileService._trash(file_obj, acting_user, deleted_at, details)

    @staticmethod
    def trash_group_files(group):
        """Trash everything *group* holds, its root folders included.

        Only for the group's own deletion: a root folder stays as long as its
        group exists. Through the root folders rather than in one queryset
        update: trashing a node moves its bytes out of the live tree.
        """
        now = timezone.now()
        roots = File.objects.filter(
            group=group, deleted_at__isnull=True, parent__isnull=True
        ).select_related("owner")
        for root in roots:
            FileService._trash(root, None, now, None)
        # Anything the group owns outside those roots (legacy rows re-parented
        # by hand) still has to leave the live listing.
        File.objects.filter(group=group, deleted_at__isnull=True).update(deleted_at=now)

    @staticmethod
    def _trash(file_obj, acting_user, deleted_at, details):
        count = file_obj.soft_delete(deleted_at=deleted_at)
        record_event(
            file_obj,
            acting_user,
            FileEvent.Action.DELETED,
            {"cascade_count": count, **(details or {})},
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
        hard_delete_tree(file_obj)

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @staticmethod
    def is_group_root(file_obj):
        """Whether *file_obj* is a group's root folder, which stays where it is."""
        return bool(file_obj.group_id) and file_obj.parent_id is None

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
    def find_name_conflict(owner, parent, name, *, exclude_pk=None):
        """Return the live file already using *name* in that folder, or None."""
        return _name_helpers.find_name_conflict(
            owner, parent, name, exclude_pk=exclude_pk
        )

    @staticmethod
    def available_file_name(owner, parent, name, *, avoiding=()):
        """*name*, or the first free ``name (Copy N).ext`` variant in that folder."""
        return _name_helpers.available_file_name(owner, parent, name, avoiding=avoiding)

    @staticmethod
    def validate_move_target(file_obj, new_parent, user=None):
        """Raise ``ValueError`` if *user* may not move *file_obj* under *new_parent*.

        The target must be a folder *user* could create in. Without a user the
        owner stands in for a personal target, and a group target is refused:
        nothing proves a membership.
        """
        if new_parent is None:
            return
        if FileService.is_group_root(file_obj):
            raise GroupRootFolder(gettext("A group's root folder cannot be moved."))
        _name_helpers.validate_move_target(file_obj, new_parent)
        if user is None:
            if new_parent.group_id:
                raise ValueError(gettext("You are not a member of this group."))
            user = file_obj.owner
        if not can_create_in(user, new_parent):
            raise ValueError(gettext("You cannot move items into this folder."))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @staticmethod
    def read_text_content(file_obj, *, max_bytes=32_768):
        """Read and return the text content of a file."""
        return _content_helpers.read_text_content(file_obj, max_bytes=max_bytes)

    @staticmethod
    def read_image_content(file_obj, *, max_bytes=10_485_760):
        """Read and return the raw bytes of an image file."""
        return _content_helpers.read_image_content(file_obj, max_bytes=max_bytes)
