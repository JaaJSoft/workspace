"""Centralized file management service.

The heavy lifting lives in helper modules in this package
(``_storage_ops``, ``_names``, ``_content``); ``FileService`` here is a
thin facade that exposes a stable public surface to views, serializers,
sync, and tasks.
"""

import enum
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from workspace.common.logging import scrub

from ..metrics import FILES_UPLOAD_BYTES
from ..models import File, FileEvent
from . import _content as _content_helpers
from . import _names as _name_helpers
from . import _storage_ops as _storage
from .events import record_event
from .hard_delete import hard_delete_tree
from .processing_failures import clear_failures
from .quota import check_write_allowed, subtree_bytes
from .scanning.policy import QuarantinedSource, exclude_blocked, is_blocked


class GroupRootFolder(ValueError):
    """A group's root folder was asked to leave its place.

    The root is the group's whole tree: moving it re-owns the subtree to the
    mover and drops the group, trashing it takes every member's files with it.
    """


class GroupMismatch(ValueError):
    """A node was asked to join a group other than its parent folder's."""

    def __init__(self, message="The group must be the parent folder's group."):
        super().__init__(message)


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

        Single source of truth for the access branches (owned, group folder,
        shared with the user, shared with one of their groups, shared with a
        project they can open), consumed by
        both ``accessible_files_q`` (ORed into one Q across a join) and
        ``accessible_file_ids`` (each branch a separately indexed UNION arm). Defining the branches once keeps the two
        permission paths from drifting - a divergence would mean a leak or a
        hole in one of them.
        """
        from workspace.projects.queries import user_project_ids

        # Owning a group node grants nothing once its owner has left the
        # group: the group branch is what covers the group nodes they own.
        return (
            {"owner": user, "group__isnull": True},
            {"group__in": user.groups.all()},
            {"shares__shared_with": user},
            {"shares__shared_with_group__in": user.groups.all()},
            {"shares__shared_with_project__in": user_project_ids(user)},
        )

    @staticmethod
    def access_arms(user):
        """``_access_branches`` with the group-folder branch split per group.

        ``group_id IN (...)`` cannot be read in index order, so a caller that
        wants the newest rows of each arm through an index range needs one
        equality per group.
        """
        arms = []
        for branch in FileService._access_branches(user):
            if branch.keys() == {"group__in"}:
                group_ids = branch["group__in"].values_list("pk", flat=True)
                arms.extend({"group_id": group_id} for group_id in group_ids)
            else:
                arms.append(branch)
        return arms

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
    def accessible_file_ids(user, *, include_deleted=True):
        """Return a values queryset of ids of all files *user* can access.

        Same semantics as ``accessible_files_q`` (owned + group + shared,
        ``deleted_at`` not filtered by default), but built as a UNION of
        independently indexed queries, one per access branch. The Q form ORs
        across a join (``shares__shared_with``), which defeats per-branch
        index use and
        forces a full scan of the files table - O(total files) on every
        call. The UNION stays proportional to what the user can actually
        see, so hot paths (activity feed) should prefer this helper as a
        ``pk__in=`` / ``file_id__in=`` source.

        ``include_deleted=False`` excludes trashed files by filtering
        ``deleted_at`` inside each UNION arm, where it pairs with the
        composite indexes (owner/deleted_at, group/deleted_at). Callers must
        NOT add ``deleted_at__isnull=True`` to the outer query instead:
        ``deleted_at IS NULL`` matches almost every row, and the planner is
        prone to driving the whole query from that low-selectivity index -
        a full-table scan that this helper exists to avoid.

        UNION querysets are terminal: no further ``filter()``/``annotate()``
        is possible. When the access filter must compose with other
        conditions in the same query, keep using ``accessible_files_q``.
        The empty ``order_by()`` is required twice - ``File`` has a default
        ``Meta.ordering``. On each branch because ORDER BY is invalid inside
        a compound subquery, and on the combined queryset because a combined
        queryset falls back to ``Meta.ordering``, whose columns aren't in the
        single-column result set - DatabaseError at iteration.
        """
        live = {} if include_deleted else {"deleted_at__isnull": True}
        arms = [
            File.objects.filter(**branch, **live)
            .order_by()
            .values_list("pk", flat=True)
            for branch in FileService._access_branches(user)
        ]
        return arms[0].union(*arms[1:]).order_by()

    @staticmethod
    def user_files_qs(user):
        """Return a queryset of active (non-deleted) personal files owned by the user."""
        return File.objects.filter(
            owner=user, group__isnull=True, deleted_at__isnull=True
        )

    @staticmethod
    def owned_files_q(user):
        """``owned_files_qs`` as a Q, for a query that already reads ``File``.

        *user* may be a user or a user id: the filter needs no row of theirs.
        """
        from django.contrib.auth.models import Group
        from django.db.models import Q

        return Q(owner=user) & (
            Q(group__isnull=True) | Q(group__in=Group.objects.filter(user=user))
        )

    @staticmethod
    def owned_files_qs(user):
        """The nodes *user* owns and still manages, ``deleted_at`` not filtered.

        Their personal nodes, plus the group nodes they created in groups they
        still belong to - not those of a group they have left, which stay with
        the group.
        """
        return File.objects.filter(FileService.owned_files_q(user))

    @staticmethod
    def restorable_files_qs(user):
        """The nodes *user* may restore or purge once trashed, ``deleted_at``
        not filtered.

        Their personal nodes, plus every node of the groups they belong to,
        whoever trashed it: the queryset form of ``trash_permissions_bulk``.
        """
        from django.db.models import Q

        return File.objects.filter(
            Q(owner=user, group__isnull=True) | Q(group__in=user.groups.all())
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
        from workspace.files.services.scanning.policy import with_scan

        return with_scan(
            queryset.annotate(
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
        )

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
            detection = detect_from_stream(content)
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
        """Create a new folder record."""
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
        _name_helpers.insert_node(folder)
        FileService._ensure_folder_on_storage(folder)
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
        """Register a file that already exists on disk (used by sync)."""
        from workspace.files.services.content_hash import hash_storage_file
        from workspace.files.services.detection import detect_from_name

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

        new_owner = (
            acting_user if (old_group and not new_group and acting_user) else None
        )

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
            raise GroupRootFolder("A group's root folder cannot be moved.")

        new_group = new_parent.group if new_parent else None
        old_group = file_obj.group

        # A node leaving a group folder becomes the personal node of whoever
        # moved it, so the destination namespace is theirs, not the current
        # owner's.
        new_owner = (
            acting_user if (old_group and not new_group and acting_user) else None
        )

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
            if old_group and not new_group and new_owner:
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
        if old_name == new_name:
            return file_obj
        if FileService.is_group_root(file_obj):
            raise GroupRootFolder("A group's root folder cannot be renamed.")

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
        """
        from workspace.files.services.content_hash import upload_hash
        from workspace.files.services.detection import (
            detect_from_stream,
            refine_with_name,
        )
        from workspace.files.services.filetype import pin_viewer_for_upload

        check_write_allowed(
            owner=file_obj.owner_id,
            group=file_obj.group_id,
            additional_bytes=content.size - (file_obj.size or 0),
        )

        detection = detect_from_stream(content)
        # Honour the extension when Magika's content label is generic (a sparse
        # ".md" reads as txt) so an edited note keeps type=markdown.
        file_type = refine_with_name(detection.label, name or file_obj.name)
        content_field = File._meta.get_field("content")
        # Where the file's own name puts its blob, whatever the upload was
        # called. Resolved early because the row write has to name the path in
        # the same statement that claims the row.
        storage_path = content_field.storage.get_available_name(
            content_field.generate_filename(file_obj, file_obj.name),
            max_length=content_field.max_length,
        )
        fields = {
            "content": storage_path,
            "content_hash": upload_hash(content),
            "size": content.size,
            "mime_type": mime_type or detection.mime_type,
            "type": file_type,
            "category": detection.group or "unknown",
            "viewer": pin_viewer_for_upload(
                file_type, mime_type or getattr(content, "content_type", None)
            ),
            "has_thumbnail": False,
            "processing_status": File.ProcessingStatus.PENDING,
            "updated_at": timezone.now(),
        }
        with transaction.atomic():
            FileService._write_content_row(file_obj, acting_user, expected_hash, fields)
            stored = _storage.replace_blob(content_field.storage, storage_path, content)
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
        fields = {
            "content": "",
            "content_hash": "",
            "size": None,
            "has_thumbnail": False,
            "processing_status": File.ProcessingStatus.READY,
            "updated_at": timezone.now(),
        }
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
        """
        from workspace.files.services.detection import detect_from_name

        detection = detect_from_name(file_obj.name)
        fields = {
            "content": storage_path,
            "content_hash": content_hash,
            "size": size,
            "mime_type": detection.mime_type,
            "type": detection.label,
            "category": detection.group or "unknown",
            "has_thumbnail": False,
            "processing_status": File.ProcessingStatus.PENDING,
            "updated_at": timezone.now(),
        }
        FileService._write_content_row(file_obj, acting_user, expected_hash, fields)
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
    def copy(file_obj, target_parent, owner, *, acting_user=None):
        """Recursively copy a file or folder to *target_parent*.

        Blocked descendants of a folder are left behind; a blocked file raises
        ``QuarantinedSource``.
        """
        FileService.check_copy_allowed(file_obj)
        check_write_allowed(
            owner=owner,
            group=target_parent.group if target_parent is not None else None,
            # copy_node only duplicates live descendants.
            additional_bytes=subtree_bytes(file_obj, include_trashed=False),
        )
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
        if FileService.is_group_root(file_obj):
            raise GroupRootFolder("A group's root folder cannot be deleted.")
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
        hard_delete_tree(file_obj)

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    @staticmethod
    def get_permissions_bulk(user, file_objects):
        """Return ``{file.pk: FilePermission | None}`` for many files at once.

        Same semantics as :meth:`get_permission`, but the group and share
        lookups are batched: at most two queries for the whole set instead
        of up to two per file. A file reached through both a user share and
        a group share gets the higher of the two permissions.
        """
        return FileService._permissions_bulk(user, file_objects, in_trash=False)

    @staticmethod
    def trash_permissions_bulk(user, file_objects):
        """``get_permissions_bulk`` as the trash sees it.

        The same answer for a live node. A trashed node grants nothing to
        anyone but its owner, except a group's: the trash is where the group
        recovers what one of its members deleted, so every current member gets
        EDIT on it - enough to preview, restore and purge it. A former member
        still gets nothing.
        """
        return FileService._permissions_bulk(user, file_objects, in_trash=True)

    @staticmethod
    def trash_permission(user, file_obj):
        """:meth:`trash_permissions_bulk` for one node."""
        return FileService.trash_permissions_bulk(user, [file_obj])[file_obj.pk]

    @staticmethod
    def _permissions_bulk(user, file_objects, *, in_trash):
        from workspace.files.models import FileShare

        permissions = {}
        group_candidates = []
        share_candidates = []
        for file_obj in file_objects:
            if file_obj.group_id:
                # Ownership of a group node only counts while the owner is
                # still in the group, so it waits for the membership lookup.
                group_candidates.append(file_obj)
            elif file_obj.owner_id == user.id:
                permissions[file_obj.pk] = FilePermission.MANAGE
            elif file_obj.deleted_at is not None:
                permissions[file_obj.pk] = None
            else:
                share_candidates.append(file_obj)

        if group_candidates:
            group_ids = set(user.groups.values_list("id", flat=True))
            for file_obj in group_candidates:
                if file_obj.group_id in group_ids:
                    if file_obj.owner_id == user.id:
                        permissions[file_obj.pk] = FilePermission.MANAGE
                    elif file_obj.deleted_at is None or in_trash:
                        permissions[file_obj.pk] = FilePermission.EDIT
                    else:
                        permissions[file_obj.pk] = None
                elif file_obj.deleted_at is not None:
                    permissions[file_obj.pk] = None
                else:
                    share_candidates.append(file_obj)

        if share_candidates:
            shares = {}
            for file_id, permission in (
                FileShare.objects.reaching(user)
                .filter(file_id__in=[f.pk for f in share_candidates])
                .values_list("file_id", "permission")
            ):
                granted = (
                    FilePermission.WRITE if permission == "rw" else FilePermission.VIEW
                )
                shares[file_id] = max(shares.get(file_id, granted), granted)
            for file_obj in share_candidates:
                permissions[file_obj.pk] = shares.get(file_obj.pk)

        return permissions

    @staticmethod
    def get_permission(user, file_obj):
        """Return the access permission level for *user* on *file_obj*."""
        return FileService.get_permissions_bulk(user, [file_obj])[file_obj.pk]

    @staticmethod
    def can_access(user, file_obj):
        """Check whether *user* can access *file_obj*."""
        return FileService.get_permission(user, file_obj) is not None

    @staticmethod
    def can_create_in(user, parent):
        """Whether *user* may create a file or folder directly under *parent*.

        Needs a live folder and EDIT on it: a read-write share only grants
        WRITE, which covers the shared node's content, not new rows under it.
        """
        return (
            parent.node_type == File.NodeType.FOLDER
            and parent.deleted_at is None
            and (FileService.get_permission(user, parent) or 0) >= FilePermission.EDIT
        )

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

    @staticmethod
    def resolve_accessible_files(user, file_uuids):
        """Resolve *file_uuids* to live, accessible ``File`` rows, or None.

        Deduplicates the input, keeps only non-trashed regular files the
        user can access (permissions checked in one bulk pass), and returns
        None as soon as a single uuid is unknown, trashed, a folder, blocked
        by the malware policy or out of reach - callers attach
        all-or-nothing and must not leak which of the five it was.

        The malware exclusion belongs here rather than at each caller: every
        one of them copies the blob into a table with no scan row of its own
        (a chat attachment, an SMTP payload, a task attachment), so a blocked
        file that got through would never be re-examined.
        """
        file_uuids = list(dict.fromkeys(file_uuids))
        if not file_uuids:
            return []
        candidates = list(
            exclude_blocked(
                File.objects.filter(
                    uuid__in=file_uuids,
                    node_type=File.NodeType.FILE,
                    deleted_at__isnull=True,
                )
            )
        )
        permissions = FileService.get_permissions_bulk(user, candidates)
        accessible = [f for f in candidates if permissions[f.pk] is not None]
        if len(accessible) != len(file_uuids):
            return None
        return accessible

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
            raise GroupRootFolder("A group's root folder cannot be moved.")
        _name_helpers.validate_move_target(file_obj, new_parent)
        if user is None:
            if new_parent.group_id:
                raise ValueError("You are not a member of this group.")
            user = file_obj.owner
        if not FileService.can_create_in(user, new_parent):
            raise ValueError("You cannot move items into this folder.")

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
    def _folder_storage_path(folder):
        return _storage.folder_storage_path(folder)

    @staticmethod
    def _ensure_folder_on_storage(folder):
        return _storage.ensure_folder_on_storage(folder)

    @staticmethod
    def _parent_storage_path(owner, parent):
        return _storage.parent_storage_path(owner, parent)

    @staticmethod
    def _unique_copy_name(base_name, node_type, existing_names):
        return _storage.unique_copy_name(base_name, node_type, existing_names)
