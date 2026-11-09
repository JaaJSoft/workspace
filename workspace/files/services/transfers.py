"""Copying or moving one node into a folder, on a user's behalf.

Every path that copies or moves a node for a user comes here, so the guards
and the name-conflict rules exist once. A refusal the caller can explain is a
:class:`~.refusals.Refused`; anything else that escapes is a failure nobody
planned for.
"""

from collections import namedtuple
from enum import StrEnum

from django.db import transaction

from ..models import File
from ._names import find_node_conflict
from .announcements import announce_replaced
from .files import FilePermission, FileService, GroupRootFolder
from .refusals import (
    Refusal,
    Refused,
    refusals,
    require_action,
    require_destination,
    require_unlocked,
)


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


# ``node`` is what the transfer left in the destination: the copy, the moved
# node, or the file whose content was replaced. None when it was skipped.
TransferResult = namedtuple("TransferResult", "outcome node")


def _require_target(user, node, parent):
    require_destination(user, parent)
    if parent is not None and node.node_type == File.NodeType.FOLDER:
        # ``path`` is denormalized and may still be None on a folder created
        # a moment ago.
        node_path = node.path or node.get_path()
        parent_path = parent.path or parent.get_path()
        if parent.pk == node.pk or parent_path.startswith(f"{node_path}/"):
            raise Refused(Refusal.INTO_ITSELF)


def _replace_with(user, existing, source):
    """Write *source*'s content into *existing*, which keeps its row."""
    if not source.content:
        raise Refused(Refusal.NO_CONTENT)
    permission = FileService.get_permission(user, existing)
    if permission is None or permission < FilePermission.WRITE:
        raise Refused(Refusal.NOT_REPLACEABLE)
    require_unlocked(user, existing)
    with refusals():
        FileService.replace_content_from(existing, source, acting_user=user)


def copy_item(user, node, parent, on_conflict=OnConflict.RENAME, *, checkpoint=None):
    """Copy *node* under *parent* (None for the user's root).

    Only a file can replace, skip or be refused on a taken name; a folder or
    a shortcut is always stored under a free one. *checkpoint* is handed to
    ``FileService.copy``.
    """
    require_action("copy", user, node)
    _require_target(user, node, parent)

    if on_conflict != OnConflict.RENAME and node.node_type == File.NodeType.FILE:
        existing = FileService.find_name_conflict(
            user, parent, node.name, exclude_pk=node.pk
        )
        if existing is not None:
            if on_conflict == OnConflict.SKIP:
                return TransferResult(Outcome.SKIPPED, None)
            if on_conflict == OnConflict.ERROR:
                raise Refused(Refusal.NAME_TAKEN)
            _replace_with(user, existing, node)
            announce_replaced(existing, user)
            return TransferResult(Outcome.REPLACED, existing)

    with refusals():
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
    require_action("cut", user, node)
    if node.parent_id == (parent.pk if parent is not None else None):
        return TransferResult(Outcome.SKIPPED, None)
    _require_target(user, node, parent)
    try:
        FileService.validate_move_target(node, parent, user=user)
    except GroupRootFolder as error:
        raise Refused(Refusal.GROUP_ROOT) from error
    except ValueError as error:
        raise Refused(Refusal.TARGET) from error
    require_unlocked(user, node)

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
            with refusals():
                FileService.soft_delete(node, acting_user=user)
            return TransferResult(Outcome.REPLACED, conflict)
        if on_conflict == OnConflict.RENAME:
            # Renamed before it leaves, so the name has to be free in the
            # folder it is still in as well.
            free_name = FileService.available_file_name(
                owner, parent, node.name, avoiding=(node.parent,)
            )
            with refusals():
                FileService.rename(node, free_name, acting_user=user)
            outcome = Outcome.RENAMED
        else:
            raise Refused(Refusal.NAME_TAKEN)

    try:
        with refusals():
            FileService.move(node, parent, acting_user=user)
    except Refused as refusal:
        # A name taken between the look and the move, or held by a folder.
        if refusal.reason == Refusal.NAME_TAKEN and on_conflict == OnConflict.SKIP:
            return TransferResult(Outcome.SKIPPED, None)
        raise
    return TransferResult(outcome, node)
