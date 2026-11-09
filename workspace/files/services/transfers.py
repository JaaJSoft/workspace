"""Copying or moving one node into a folder, on a user's behalf.

Every path that pastes comes here - the copy endpoint and the batch
operations - so the guards and the name-conflict rules exist once.

A refusal the caller can explain is a :class:`TransferRefused` carrying a
fixed code; the exception it came from stays reachable as ``__cause__``.
Anything else that escapes is a failure nobody planned for.
"""

from collections import namedtuple
from contextlib import contextmanager
from enum import StrEnum

from django.db import transaction

from ..models import File
from ._names import NameTaken, find_node_conflict
from .announcements import announce_replaced
from .files import FilePermission, FileService, GroupRootFolder
from .locking import LockConflict, StaleContent, conflicting_lock
from .quota import QuotaExceeded
from .scanning.policy import QuarantinedSource


class OnConflict(StrEnum):
    """What to do when the destination already holds the name."""

    RENAME = "rename"
    REPLACE = "replace"
    SKIP = "skip"
    ERROR = "error"


class Outcome(StrEnum):
    DONE = "done"
    REPLACED = "replaced"
    RENAMED = "renamed"
    SKIPPED = "skipped"


class Refusal(StrEnum):
    NOT_ALLOWED = "not_allowed"
    TARGET = "target"
    INTO_ITSELF = "into_itself"
    NAME_TAKEN = "name_taken"
    NO_CONTENT = "no_content"
    NOT_REPLACEABLE = "not_replaceable"
    LOCKED = "locked"
    STALE = "stale"
    QUARANTINED = "quarantined"
    QUOTA = "quota"
    GROUP_ROOT = "group_root"


# ``node`` is what the transfer left in the destination: the copy, the moved
# node, or the file whose content was replaced. None when it was skipped.
TransferResult = namedtuple("TransferResult", "outcome node")


class TransferRefused(Exception):
    """A transfer the rules turned down, for a reason with a name."""

    def __init__(self, reason, *, holder=None, current_hash=""):
        self.reason = reason
        self.holder = holder
        self.current_hash = current_hash
        super().__init__(str(reason))


@contextmanager
def _refusals():
    try:
        yield
    except QuarantinedSource as error:
        raise TransferRefused(Refusal.QUARANTINED) from error
    except QuotaExceeded as error:
        raise TransferRefused(Refusal.QUOTA) from error
    except LockConflict as error:
        raise TransferRefused(Refusal.LOCKED, holder=error.holder) from error
    except StaleContent as error:
        raise TransferRefused(Refusal.STALE, current_hash=error.current_hash) from error
    except GroupRootFolder as error:
        raise TransferRefused(Refusal.GROUP_ROOT) from error
    except NameTaken as error:
        raise TransferRefused(Refusal.NAME_TAKEN) from error


def _require_action(action_id, user, node):
    # The registry's action modules import the services package.
    from workspace.files.actions import ActionRegistry

    if not ActionRegistry.is_action_available(
        action_id, user, node, permission=FileService.get_permission(user, node)
    ):
        raise TransferRefused(Refusal.NOT_ALLOWED)


def _require_target(user, node, parent):
    if parent is None:
        return
    if not FileService.can_create_in(user, parent):
        raise TransferRefused(Refusal.TARGET)
    if node.node_type == File.NodeType.FOLDER:
        # ``path`` is denormalized and may still be None on a folder created
        # a moment ago.
        node_path = node.path or node.get_path()
        parent_path = parent.path or parent.get_path()
        if parent.pk == node.pk or parent_path.startswith(f"{node_path}/"):
            raise TransferRefused(Refusal.INTO_ITSELF)


def _replace_with(user, existing, source):
    """Write *source*'s content into *existing*, which keeps its row."""
    if not source.content:
        raise TransferRefused(Refusal.NO_CONTENT)
    permission = FileService.get_permission(user, existing)
    if permission is None or permission < FilePermission.WRITE:
        raise TransferRefused(Refusal.NOT_REPLACEABLE)
    holder = conflicting_lock(existing, user)
    if holder is not None:
        raise TransferRefused(Refusal.LOCKED, holder=holder)
    with _refusals():
        FileService.replace_content_from(existing, source, acting_user=user)


def copy_item(user, node, parent, on_conflict=OnConflict.RENAME, *, checkpoint=None):
    """Copy *node* under *parent* (None for the user's root).

    Only a file can replace, skip or be refused on a taken name; a folder or
    a shortcut is always stored under a free one. *checkpoint* is handed to
    ``FileService.copy``.
    """
    _require_action("copy", user, node)
    _require_target(user, node, parent)

    if on_conflict != OnConflict.RENAME and node.node_type == File.NodeType.FILE:
        existing = FileService.find_name_conflict(
            user, parent, node.name, exclude_pk=node.pk
        )
        if existing is not None:
            if on_conflict == OnConflict.SKIP:
                return TransferResult(Outcome.SKIPPED, None)
            if on_conflict == OnConflict.ERROR:
                raise TransferRefused(Refusal.NAME_TAKEN)
            _replace_with(user, existing, node)
            announce_replaced(existing, user)
            return TransferResult(Outcome.REPLACED, existing)

    with _refusals():
        copied = FileService.copy(
            node, parent, user, acting_user=user, checkpoint=checkpoint
        )
    # A duplicate made next to its source is named "(Copy)" by design: that
    # is not a conflict the user has to hear about.
    same_folder = node.parent_id == (parent.pk if parent is not None else None)
    renamed = copied.name != node.name and not same_folder
    return TransferResult(Outcome.RENAMED if renamed else Outcome.DONE, copied)


def move_item(user, node, parent, on_conflict=OnConflict.ERROR):
    """Move *node* under *parent* (None for the user's root)."""
    result = _move(user, node, parent, on_conflict)
    if result.outcome == Outcome.REPLACED:
        # Outside the transaction: nobody should be told about a replacement
        # that could still roll back.
        announce_replaced(result.node, user)
    return result


@transaction.atomic
def _move(user, node, parent, on_conflict):
    _require_action("cut", user, node)
    if node.parent_id == (parent.pk if parent is not None else None):
        return TransferResult(Outcome.SKIPPED, None)
    _require_target(user, node, parent)
    try:
        FileService.validate_move_target(node, parent, user=user)
    except GroupRootFolder as error:
        raise TransferRefused(Refusal.GROUP_ROOT) from error
    except ValueError as error:
        raise TransferRefused(Refusal.TARGET) from error
    holder = conflicting_lock(node, user)
    if holder is not None:
        raise TransferRefused(Refusal.LOCKED, holder=holder)

    # A node leaving a group folder becomes the mover's own, so the names it
    # competes with are theirs - the rule FileService.move applies.
    leaving_group = node.group_id and (parent is None or not parent.group_id)
    owner = user if leaving_group else node.owner

    outcome = Outcome.DONE
    if node.node_type == File.NodeType.FILE:
        conflict = FileService.find_name_conflict(
            owner, parent, node.name, exclude_pk=node.pk
        )
    elif node.node_type == File.NodeType.SHORTCUT:
        conflict = find_node_conflict(owner, parent, node.name, exclude_pk=node.pk)
    else:
        conflict = None

    if conflict is not None:
        if on_conflict == OnConflict.SKIP:
            return TransferResult(Outcome.SKIPPED, None)
        if on_conflict == OnConflict.REPLACE and node.node_type == File.NodeType.FILE:
            _replace_with(user, conflict, node)
            with _refusals():
                FileService.soft_delete(node, acting_user=user)
            return TransferResult(Outcome.REPLACED, conflict)
        if on_conflict == OnConflict.RENAME:
            # Renamed before it leaves, so the name has to be free in the
            # folder it is still in as well.
            free_name = FileService.available_file_name(
                owner, parent, node.name, avoiding=(node.parent,)
            )
            with _refusals():
                FileService.rename(node, free_name, acting_user=user)
            outcome = Outcome.RENAMED
        else:
            raise TransferRefused(Refusal.NAME_TAKEN)

    try:
        with _refusals():
            FileService.move(node, parent, acting_user=user)
    except TransferRefused as refusal:
        # A name taken between the look and the move, or held by a folder.
        if refusal.reason == Refusal.NAME_TAKEN and on_conflict == OnConflict.SKIP:
            return TransferResult(Outcome.SKIPPED, None)
        raise
    return TransferResult(outcome, node)
