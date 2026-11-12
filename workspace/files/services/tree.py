"""The tree's own operations: creating, moving, renaming and copying nodes.

Each one writes the rows first and the bytes last, records the event, and
refuses what would break the tree (a taken name, a full bucket, a group's
root folder leaving its place). None of them asks who is acting: a caller
working on a user's behalf goes through ``transfers`` and ``trash``, which
ask the action registry and the lock before calling here.
"""

import logging
from contextlib import nullcontext

from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext

from workspace.common.logging import scrub

from ..metrics import FILES_UPLOAD_BYTES
from ..models import File, FileEvent, canonical_name
from ..queries import can_create_in
from . import bucket_usage, detection, names, storage_ops
from .content_hash import hash_storage_file, upload_hash
from .events import record_event
from .filetype import pin_viewer_for_upload
from .quota import check_write_allowed, subtree_bytes
from .scanning.policy import check_copy_allowed

logger = logging.getLogger(__name__)


class GroupRootFolder(ValueError):
    """A group's root folder was asked to leave its place.

    The root is the group's whole tree: moving it re-owns the subtree to the
    mover and drops the group, trashing it takes every member's files with it.
    """


class GroupMismatch(ValueError):
    """A node was asked to join a group other than its parent folder's."""

    def __init__(self, message="The group must be the parent folder's group."):
        super().__init__(message)


def new_owner_on_move(file_obj, new_parent, acting_user):
    """Who a move under *new_parent* hands *file_obj* to, or None.

    A node leaving a group folder becomes the personal node of whoever moved
    it; any other move leaves its owner alone.
    """
    leaves_group = file_obj.group_id and not (
        new_parent is not None and new_parent.group_id
    )
    return acting_user if (leaves_group and acting_user) else None


def is_group_root(file_obj):
    """Whether *file_obj* is a group's root folder, which stays where it is."""
    return bool(file_obj.group_id) and file_obj.parent_id is None


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
    name = canonical_name(name)
    group = node_group(parent, group)

    # Before the detection and hashing passes read the stream. Not the
    # guarantee - the INSERT is - but it spares the loser of a known
    # collision both passes.
    if not keep_both:
        names.check_name_available(owner, parent, name, File.NodeType.FILE)
    if check_quota:
        check_write_allowed(
            owner=owner,
            group=group,
            additional_bytes=content.size if content is not None else 0,
        )

    if content is not None:
        detected = detection.detect_from_stream(content, name)
        content_hash = upload_hash(content)
    else:
        detected = detection.detect_from_name(name)
        content_hash = ""

    # Magika reads a sparse ".md" as txt; honour the extension for the
    # stored label so notes stay discoverable (the notes browser and the
    # [[ search both filter on type=markdown).
    label = detection.refine_with_name(detected.label, name)

    # The declared media type is the only signal that can tell an
    # audio-only container from a video one; acting on it is a display
    # decision, so it pins a viewer instead of rewriting the detection.
    if viewer is None:
        declared_mime = mime_type or getattr(content, "content_type", None)
        viewer = pin_viewer_for_upload(label, declared_mime)

    if not mime_type:
        mime_type = detected.mime_type

    size = content.size if content is not None else None

    file_obj = File(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        parent=parent,
        mime_type=mime_type or "application/octet-stream",
        type=label,
        category=detected.group or "unknown",
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
        names.insert_node_keeping_both(file_obj, content)
    else:
        names.insert_node(file_obj, content)
    if size:
        FILES_UPLOAD_BYTES.inc(size)
    record_event(file_obj, acting_user or owner, FileEvent.Action.CREATED)
    return file_obj


def create_folder(
    owner, name, parent=None, *, icon=None, color=None, group=None, acting_user=None
):
    """Create a new folder record and its directory, or neither."""
    name = canonical_name(name)
    group = node_group(parent, group)

    names.check_name_available(owner, parent, name, File.NodeType.FOLDER)

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
        names.insert_node(folder)
        storage_ops.ensure_folder_on_storage(folder)
    record_event(folder, acting_user or owner, FileEvent.Action.CREATED)
    return folder


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
    name = canonical_name(name)
    detected = detection.detect_from_name(name)
    if not mime_type:
        mime_type = detected.mime_type

    names.check_name_available(owner, parent, name, File.NodeType.FILE)

    file_obj = File(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        parent=parent,
        mime_type=mime_type,
        type=detected.label,
        category=detected.group or "unknown",
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
        logger.warning("Cannot hash disk file %s: %s", scrub(content_path), scrub(e))
    names.insert_node(file_obj)
    record_event(file_obj, acting_user, FileEvent.Action.CREATED)
    return file_obj


def check_move_allowed(file_obj, new_parent, *, acting_user=None):
    """Raise ``QuotaExceeded`` when moving *file_obj* under *new_parent* would not fit."""
    new_group = new_parent.group if new_parent else None
    old_group = file_obj.group
    new_owner = new_owner_on_move(file_obj, new_parent, acting_user)

    # Only a group change moves bytes between buckets. The owner charged
    # is the one the move will assign, not necessarily the current one.
    if (old_group.pk if old_group else None) != (new_group.pk if new_group else None):
        check_write_allowed(
            owner=new_owner or file_obj.owner_id,
            group=new_group,
            # Trashed descendants travel with the subtree.
            additional_bytes=subtree_bytes(file_obj, include_trashed=True),
        )


def validate_move_target(file_obj, new_parent, user=None):
    """Raise ``ValueError`` if *user* may not move *file_obj* under *new_parent*.

    The target must be a folder *user* could create in. Without a user the
    owner stands in for a personal target, and a group target is refused:
    nothing proves a membership.
    """
    if new_parent is None:
        return
    if is_group_root(file_obj):
        raise GroupRootFolder(gettext("A group's root folder cannot be moved."))
    names.validate_move_target(file_obj, new_parent)
    if user is None:
        if new_parent.group_id:
            raise ValueError(gettext("You are not a member of this group."))
        user = file_obj.owner
    if not can_create_in(user, new_parent):
        raise ValueError(gettext("You cannot move items into this folder."))


@transaction.atomic
def move(file_obj, new_parent, *, acting_user=None):
    """Move a file or folder to a new parent, handling physical storage."""
    old_parent_id = file_obj.parent_id
    new_parent_id = new_parent.pk if new_parent else None
    if old_parent_id == new_parent_id:
        return
    if is_group_root(file_obj):
        raise GroupRootFolder(gettext("A group's root folder cannot be moved."))

    new_group = new_parent.group if new_parent else None
    old_group = file_obj.group

    # The destination namespace is the new owner's, not the current one's.
    new_owner = new_owner_on_move(file_obj, new_parent, acting_user)

    names.check_name_available(
        new_owner or file_obj.owner,
        new_parent,
        file_obj.name,
        file_obj.node_type,
        exclude_pk=file_obj.pk,
    )
    check_move_allowed(file_obj, new_parent, acting_user=acting_user)

    # Rows first, bytes last: the rows are what reserve the name, and a
    # refusal must find the bytes where they were. Only the columns this
    # move changes - a bare save() would write back a content write or a
    # lock that landed meanwhile.
    source = storage_ops.current_node_path(file_obj)
    changes_bucket = (old_group.pk if old_group else None) != (
        new_group.pk if new_group else None
    )
    usage = (
        bucket_usage.tracking(
            lambda: File.objects.filter(file_obj._descendant_filter())
        )
        if changes_bucket
        else nullcontext()
    )
    with (
        usage,
        names.name_claim(new_owner or file_obj.owner, new_parent, file_obj.name),
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
            propagate_group(file_obj, new_group)
    if source:
        storage_ops.move_node_storage(
            file_obj, source, storage_ops.node_storage_path(file_obj)
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


def propagate_group(file_obj, group):
    """Set group on file_obj and all its descendants."""
    File.objects.filter(file_obj._descendant_filter()).update(group=group)
    file_obj.group = group


@transaction.atomic
def rename(file_obj, new_name, *, acting_user=None):
    """Rename a file or folder, moving physical storage files as needed."""
    old_name = file_obj.name
    new_name = canonical_name(new_name)
    if old_name == new_name:
        return file_obj
    if is_group_root(file_obj):
        raise GroupRootFolder(gettext("A group's root folder cannot be renamed."))

    names.check_name_available(
        file_obj.owner,
        file_obj.parent,
        new_name,
        file_obj.node_type,
        exclude_pk=file_obj.pk,
    )

    # Rows first, bytes last, as in ``move``. The name and the path it
    # derives, and nothing else.
    source = storage_ops.current_node_path(file_obj)
    file_obj.name = new_name
    with names.name_claim(file_obj.owner, file_obj.parent, new_name):
        file_obj.save(update_fields=["name"])
    if source:
        storage_ops.move_node_storage(
            file_obj, source, storage_ops.node_storage_path(file_obj)
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
    copied = storage_ops.copy_node(
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
