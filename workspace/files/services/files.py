"""Centralized file management service.

The heavy lifting lives in helper modules in this package
(``_storage_ops``, ``_names``, ``_content``); ``FileService`` here is a
thin facade that exposes a stable public surface to views, serializers,
sync, and tasks.
"""

import logging
import posixpath
import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext

from workspace.common.logging import scrub

from ..metrics import FILES_UPLOAD_BYTES
from ..models import File, FileEvent, canonical_name, file_upload_path
from ..queries import can_create_in
from . import _content as _content_helpers
from . import _names as _name_helpers
from . import _storage_ops as _storage
from .events import record_event
from .hard_delete import hard_delete_tree
from .locking import Relocated
from .processing_failures import clear_failures
from .quota import check_write_allowed, subtree_bytes
from .scanning.policy import QuarantinedSource, is_blocked


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


def _location(file_obj):
    """Everything a file's blob path is derived from.

    Its place in the tree (``file_upload_path``), and whether it is in the
    trash: trashing leaves the tree path as it was and moves the blob under
    ``trash/`` all the same.
    """
    return (
        file_obj.path,
        file_obj.group_id,
        file_obj.owner_id,
        file_obj.deleted_at,
    )


def _bucket(file_obj):
    """The quota bucket *file_obj* is charged to (see ``services.quota``)."""
    if file_obj.group_id:
        return ("group", file_obj.group_id)
    return ("user", file_obj.owner_id)


def new_owner_on_move(file_obj, new_parent, acting_user):
    """Who a move under *new_parent* hands *file_obj* to, or None.

    A node leaving a group folder becomes the personal node of whoever moved
    it; any other move leaves its owner alone.
    """
    leaves_group = file_obj.group_id and not (
        new_parent is not None and new_parent.group_id
    )
    return acting_user if (leaves_group and acting_user) else None


def _content_fields(**fields):
    """The columns a content write sets: *fields*, which describe the new
    bytes, and the reset of what was derived from the previous ones."""
    return {
        **fields,
        "has_thumbnail": False,
        "image_edit": None,
        "updated_at": timezone.now(),
    }


def _put_back_blobs(storage, source_path, storage_path, backup_path, moved, backed_up):
    """Undo ``move_content_from``'s storage moves after its rows rolled back."""
    try:
        if moved:
            storage.move(storage_path, source_path)
        if backed_up:
            storage.move(backup_path, storage_path)
    except OSError:
        logger.exception(
            "Could not restore blobs after a failed replace of %s", scrub(storage_path)
        )


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
    def update_content(
        file_obj,
        content,
        *,
        name=None,
        mime_type=None,
        acting_user=None,
        expected_hash=None,
        check_quota=True,
    ):
        """Replace a file's content, updating size, MIME type and hash.

        The row write is a conditional UPDATE, and that is the whole guarantee:
        its WHERE clause carries the lock predicate, plus the caller's
        ``expected_hash`` when there is one, and a statement that matches no
        row raises instead of writing. Checking either condition beforehand
        cannot serialise it with the write - two callers holding the same
        expectation both pass the check, and a lock taken just after it is
        never seen - whereas here the statement that verifies the conditions is
        the same one that invalidates them, so the second caller finds nothing
        to match. ``SELECT ... FOR UPDATE`` would be the obvious alternative
        and is not available: SQLite backs dev and the whole test suite, and
        has no row locks.

        The row is written before the bytes, which is the opposite of what
        ``save()`` does and is the point: the blob lives at a path derived from
        the file's name and the storage overwrites in place, so bytes committed
        ahead of a refused write would destroy the very content the refusal
        exists to protect. The two commit together, in a transaction this
        method opens itself rather than borrowing from a caller: the UPDATE
        holds the row until the bytes are down, and a storage failure takes the
        row that already points at them with it.

        Callers that pass no ``expected_hash`` keep last-write-wins against
        other writers. The lock is not theirs to opt out of.

        ``check_quota=False`` is for a caller that has already checked the
        bucket itself, as for :meth:`create_file`.
        """
        from workspace.files.services.content_hash import upload_hash
        from workspace.files.services.detection import (
            detect_from_stream,
            refine_with_name,
        )
        from workspace.files.services.filetype import pin_viewer_for_upload

        if check_quota:
            check_write_allowed(
                owner=file_obj.owner_id,
                group=file_obj.group_id,
                additional_bytes=content.size - (file_obj.size or 0),
            )

        detection = detect_from_stream(content, name or file_obj.name)
        # Honour the extension when Magika's content label is generic (a sparse
        # ".md" reads as txt) so an edited note keeps type=markdown.
        file_type = refine_with_name(detection.label, name or file_obj.name)
        content_field = File._meta.get_field("content")

        def blob_path(row):
            # In the trash the row is the only record of where the blob went:
            # a file trashed on its own keeps its trash directory when its
            # folder is trashed after it, and the file that took its name
            # rides inside the folder's.
            if row.deleted_at is not None and row.content:
                return row.content.name
            if row.deleted_at is not None:
                return _storage.node_storage_path(row)
            # Where the file's own name puts its blob, whatever the upload
            # was called.
            return content_field.storage.get_available_name(
                content_field.generate_filename(row, row.name),
                max_length=content_field.max_length,
            )

        fields = _content_fields(
            content_hash=upload_hash(content),
            size=content.size,
            mime_type=mime_type or detection.mime_type,
            type=file_type,
            category=detection.group or "unknown",
            viewer=pin_viewer_for_upload(
                file_type, mime_type or getattr(content, "content_type", None)
            ),
            processing_status=File.ProcessingStatus.PENDING,
        )
        with transaction.atomic():
            FileService._write_content_row(file_obj, acting_user, expected_hash, fields)
            # The path comes from the row as claimed, not from the instance
            # the caller loaded: a rename, a move or a trip to the trash that
            # committed since has the blob somewhere else, and the claim left
            # ``content`` as that move wrote it.
            current = FileService._claimed_row(file_obj)
            storage_path = blob_path(current)
            if current.content.name != storage_path:
                File.objects.filter(pk=file_obj.pk).update(content=storage_path)
            fields["content"] = storage_path
            stored = content_field.storage.replace(storage_path, content)
            if stored != storage_path:
                # A storage that renames around a collision moved the blob; the
                # row is ours until this block commits, so it can follow.
                File.objects.filter(pk=file_obj.pk).update(content=stored)
                fields["content"] = stored
        for field, value in fields.items():
            setattr(file_obj, field, value)
        # Ordering is load-bearing: after the write (nothing here is atomic, so a
        # fresh budget must not outlive a failed write) and before record_event,
        # whose pipeline re-runs the processors and spends that budget on
        # commit. Unguarded on purpose, unlike the processor runner's ledger
        # writes: on a request path a failed delete must surface rather than
        # leave repaired bytes silently parked.
        clear_failures(file_obj)
        if file_obj.size:
            FILES_UPLOAD_BYTES.inc(file_obj.size)
        record_event(file_obj, acting_user, FileEvent.Action.CONTENT_REPLACED)
        return file_obj

    @staticmethod
    def _claimed_row(file_obj):
        """*file_obj*'s row as it stands, read once the caller has claimed it.

        A rename, a move or a trip to the trash holds the rows it relocates
        until it commits, so a claim that had to wait for one sees it here.
        The bytes then go where the file is now: the path the caller computed
        from the instance it loaded is one that move is about to empty.
        """
        return File.objects.select_related("owner").get(pk=file_obj.pk)

    @staticmethod
    def _write_content_row(file_obj, acting_user, expected_hash, fields):
        """Write the content fields onto the row, or refuse.

        The WHERE clause is the guard. The lock predicate is always in it; the
        caller's expectation joins it when there is one, and ``""`` counts as
        an expectation - a row the hash backfill has not reached stores exactly
        that, and a caller that loaded it is entitled to say so.

        The expectation is the version being replaced, not "has anyone touched
        this row": a competing write that stored the bytes the caller started
        from leaves the hash where it was, and the caller is right to proceed -
        there is nothing of the other write left to lose.
        """
        from workspace.files.services.locking import (
            LockConflict,
            StaleContent,
            conflicting_lock,
            unlocked_for_q,
        )

        claim = File.objects.filter(pk=file_obj.pk)
        if expected_hash is not None:
            claim = claim.filter(content_hash=expected_hash)
        if claim.filter(unlocked_for_q(acting_user)).update(**fields):
            return
        # Nothing matched: re-read to say which of the two conditions failed.
        # The read trails the write, so the lock may already be gone by the
        # time it runs - which is why an expectation that still matches is the
        # thing that settles it. Only a version that actually moved is
        # staleness; anything else was the lock, whether or not the holder is
        # still there to be named.
        current = (
            File.objects.select_related("locked_by").filter(pk=file_obj.pk).first()
        )
        if current is None:
            raise StaleContent("")
        holder = conflicting_lock(current, acting_user)
        if holder is not None:
            raise LockConflict(holder)
        if expected_hash is not None and current.content_hash != expected_hash:
            raise StaleContent(current.content_hash or "")
        raise LockConflict(None)

    @staticmethod
    def clear_content(file_obj, *, acting_user=None, expected_hash=None):
        """Empty a file's content row, or refuse.

        A PATCH sending ``content: null`` destroys content as surely as one
        sending bytes, so it is decided by the same conditional statement -
        the lock predicate, plus the caller's expectation when there is one.

        The blob is left where it is, as the plain ``save()`` this replaced
        also did: the row stops pointing at it and the storage sweep collects
        it, which is what keeps a refused write from touching storage at all.
        """
        fields = _content_fields(
            content="",
            content_hash="",
            size=None,
            processing_status=File.ProcessingStatus.READY,
        )
        FileService._write_content_row(file_obj, acting_user, expected_hash, fields)
        for field, value in fields.items():
            setattr(file_obj, field, value)
        return file_obj

    @staticmethod
    def replace_content_from(target, source, *, acting_user=None, expected_hash=None):
        """Give *target* the bytes of *source*, streamed into a fresh blob.

        Same contract as ``update_content``: size, type, hash and thumbnail
        state follow the new content, the row keeps its identity, and the write
        is refused rather than landing when the row moved or is locked.
        """
        from django.core.files.base import File as DjangoFile

        FileService.check_copy_allowed(source)
        with source.content.open("rb") as src:
            return FileService.update_content(
                target,
                DjangoFile(src, name=target.name),
                name=target.name,
                mime_type=source.mime_type,
                acting_user=acting_user,
                expected_hash=expected_hash,
            )

    @staticmethod
    def move_content_from(target, source, *, acting_user=None):
        """Give *target* the bytes of *source* by moving them, then drop *source*.

        A move onto a same-name file: *target* keeps its row - its shares,
        tags, comments and history - and the moved file's blob becomes its
        content without being copied, so it charges no second copy to the
        bucket. The moved row is deleted; what it held lives on in *target*.

        Not journaled as a relocation: settling picks a side by the path a row
        names, and here both versions sit at the path *target*'s row names.
        After a crash the set-aside blob stays where the sync will list it,
        rather than being dropped on a guess.
        """
        if _bucket(target) != _bucket(source):
            # Within one bucket the source bytes are already counted.
            check_write_allowed(
                owner=target.owner_id,
                group=target.group_id,
                additional_bytes=(source.size or 0) - (target.size or 0),
            )
        storage = target.content.storage
        storage_path = file_upload_path(target, target.name)
        source_path = source.content.name
        # A rollback restores rows, not blobs: whatever sat at storage_path is
        # set aside until the commit, so a failure can put both blobs back. A
        # short name of its own: the target's name plus a suffix can pass the
        # limit a name segment has.
        backup_path = posixpath.join(
            posixpath.dirname(storage_path), f".{uuid.uuid4().hex}.bak"
        )
        backed_up = moved = False
        try:
            with transaction.atomic():
                FileService.replace_content_storage(
                    target,
                    storage_path=storage_path,
                    size=source.size,
                    content_hash=source.content_hash,
                    acting_user=acting_user,
                )
                if storage.exists(storage_path):
                    storage.move(storage_path, backup_path)
                    backed_up = True
                storage.move(source_path, storage_path)
                moved = True
                # The blob has moved, so the row's storage cleanup finds nothing.
                FileService.hard_delete(source, acting_user=acting_user)
        except BaseException:
            _put_back_blobs(
                storage, source_path, storage_path, backup_path, moved, backed_up
            )
            raise
        if backed_up:
            try:
                storage.delete(backup_path)
            except OSError:
                logger.warning("Could not remove replaced blob %s", scrub(backup_path))

    @staticmethod
    def replace_content_storage(
        file_obj,
        *,
        storage_path,
        size,
        content_hash,
        acting_user=None,
        expected_hash=None,
    ):
        """Point *file_obj* at content already written to *storage_path*.

        The caller streamed the bytes and is the only one that saw them, so it
        also supplies their *content_hash* - and, when its own client asked to
        be refused rather than to overwrite a newer version, the
        ``expected_hash`` that write is replacing. That is what lets a mounted
        client carry a precondition through an upload that takes minutes.

        The ordering constraint is the caller's here: the bytes must not be
        moved into place until this returns, or a refused write has already
        overwritten the content it was refused for.

        *storage_path* was derived from *file_obj* as the caller holds it, so
        a rename, a move or a trip to the trash that committed while the claim
        waited makes it a path that move is emptying: that refuses with
        ``Relocated``.
        """
        from workspace.files.services.detection import detect_from_name

        detection = detect_from_name(file_obj.name)
        fields = _content_fields(
            content=storage_path,
            content_hash=content_hash,
            size=size,
            mime_type=detection.mime_type,
            type=detection.label,
            category=detection.group or "unknown",
            processing_status=File.ProcessingStatus.PENDING,
        )
        with transaction.atomic():
            FileService._write_content_row(file_obj, acting_user, expected_hash, fields)
            if _location(FileService._claimed_row(file_obj)) != _location(file_obj):
                raise Relocated()
        for field, value in fields.items():
            setattr(file_obj, field, value)
        # Same ordering constraint, and the same deliberate lack of a guard, as
        # update_content.
        clear_failures(file_obj)
        if size:
            FILES_UPLOAD_BYTES.inc(size)
        record_event(file_obj, acting_user, FileEvent.Action.CONTENT_REPLACED)
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
        FileService.check_copy_allowed(file_obj)
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

    @staticmethod
    def check_copy_allowed(file_obj):
        """Raise ``QuarantinedSource`` when *file_obj*'s bytes may not be copied.

        A copy is a new row with no scan verdict of its own, so a blocked
        file copied anywhere would come back out unblocked.
        """
        if is_blocked(file_obj):
            raise QuarantinedSource()

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
