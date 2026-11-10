"""Replacing a file's content: the row first, then the bytes.

Every content write is a conditional UPDATE that carries the lock predicate,
and the caller's expectation when there is one: the statement that verifies
the conditions is the one that invalidates them, so two writers cannot both
pass. The bytes follow the row, never the other way round, because a blob
lives at the path its row derives and the storage overwrites in place.
"""

import logging
import posixpath
import uuid

from django.core.files.base import File as DjangoFile
from django.db import transaction
from django.utils import timezone

from workspace.common.logging import scrub

from ..metrics import FILES_UPLOAD_BYTES
from ..models import File, FileEvent, file_upload_path
from . import detection, locking, storage_ops
from .content_hash import upload_hash
from .events import record_event
from .filetype import pin_viewer_for_upload
from .hard_delete import hard_delete_tree
from .processing_failures import clear_failures
from .quota import check_write_allowed
from .scanning.policy import check_copy_allowed

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
    bucket itself, as for ``tree.create_file``.
    """
    if check_quota:
        check_write_allowed(
            owner=file_obj.owner_id,
            group=file_obj.group_id,
            additional_bytes=content.size - (file_obj.size or 0),
        )

    detected = detection.detect_from_stream(content, name or file_obj.name)
    # Honour the extension when Magika's content label is generic (a sparse
    # ".md" reads as txt) so an edited note keeps type=markdown.
    file_type = detection.refine_with_name(detected.label, name or file_obj.name)
    content_field = File._meta.get_field("content")

    def blob_path(row):
        # In the trash the row is the only record of where the blob went:
        # a file trashed on its own keeps its trash directory when its
        # folder is trashed after it, and the file that took its name
        # rides inside the folder's.
        if row.deleted_at is not None and row.content:
            return row.content.name
        if row.deleted_at is not None:
            return storage_ops.node_storage_path(row)
        # Where the file's own name puts its blob, whatever the upload
        # was called.
        return content_field.storage.get_available_name(
            content_field.generate_filename(row, row.name),
            max_length=content_field.max_length,
        )

    fields = _content_fields(
        content_hash=upload_hash(content),
        size=content.size,
        mime_type=mime_type or detected.mime_type,
        type=file_type,
        category=detected.group or "unknown",
        viewer=pin_viewer_for_upload(
            file_type, mime_type or getattr(content, "content_type", None)
        ),
        processing_status=File.ProcessingStatus.PENDING,
    )
    with transaction.atomic():
        _write_content_row(file_obj, acting_user, expected_hash, fields)
        # The path comes from the row as claimed, not from the instance
        # the caller loaded: a rename, a move or a trip to the trash that
        # committed since has the blob somewhere else, and the claim left
        # ``content`` as that move wrote it.
        current = _claimed_row(file_obj)
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


def _claimed_row(file_obj):
    """*file_obj*'s row as it stands, read once the caller has claimed it.

    A rename, a move or a trip to the trash holds the rows it relocates
    until it commits, so a claim that had to wait for one sees it here.
    The bytes then go where the file is now: the path the caller computed
    from the instance it loaded is one that move is about to empty.
    """
    return File.objects.select_related("owner").get(pk=file_obj.pk)


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
    claim = File.objects.filter(pk=file_obj.pk)
    if expected_hash is not None:
        claim = claim.filter(content_hash=expected_hash)
    if claim.filter(locking.unlocked_for_q(acting_user)).update(**fields):
        return
    # Nothing matched: re-read to say which of the two conditions failed.
    # The read trails the write, so the lock may already be gone by the
    # time it runs - which is why an expectation that still matches is the
    # thing that settles it. Only a version that actually moved is
    # staleness; anything else was the lock, whether or not the holder is
    # still there to be named.
    current = File.objects.select_related("locked_by").filter(pk=file_obj.pk).first()
    if current is None:
        raise locking.StaleContent("")
    holder = locking.conflicting_lock(current, acting_user)
    if holder is not None:
        raise locking.LockConflict(holder)
    if expected_hash is not None and current.content_hash != expected_hash:
        raise locking.StaleContent(current.content_hash or "")
    raise locking.LockConflict(None)


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
    _write_content_row(file_obj, acting_user, expected_hash, fields)
    for field, value in fields.items():
        setattr(file_obj, field, value)
    return file_obj


def replace_content_from(target, source, *, acting_user=None, expected_hash=None):
    """Give *target* the bytes of *source*, streamed into a fresh blob.

    Same contract as ``update_content``: size, type, hash and thumbnail
    state follow the new content, the row keeps its identity, and the write
    is refused rather than landing when the row moved or is locked.
    """
    check_copy_allowed(source)
    with source.content.open("rb") as src:
        return update_content(
            target,
            DjangoFile(src, name=target.name),
            name=target.name,
            mime_type=source.mime_type,
            acting_user=acting_user,
            expected_hash=expected_hash,
        )


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
            replace_content_storage(
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
            hard_delete_tree(source)
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
    detected = detection.detect_from_name(file_obj.name)
    fields = _content_fields(
        content=storage_path,
        content_hash=content_hash,
        size=size,
        mime_type=detected.mime_type,
        type=detected.label,
        category=detected.group or "unknown",
        processing_status=File.ProcessingStatus.PENDING,
    )
    with transaction.atomic():
        _write_content_row(file_obj, acting_user, expected_hash, fields)
        if _location(_claimed_row(file_obj)) != _location(file_obj):
            raise locking.Relocated()
    for field, value in fields.items():
        setattr(file_obj, field, value)
    # Same ordering constraint, and the same deliberate lack of a guard, as
    # update_content.
    clear_failures(file_obj)
    if size:
        FILES_UPLOAD_BYTES.inc(size)
    record_event(file_obj, acting_user, FileEvent.Action.CONTENT_REPLACED)
    return file_obj
