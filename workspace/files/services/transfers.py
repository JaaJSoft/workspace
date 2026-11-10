"""Copying or moving one node into a folder, on a user's behalf.

Every path that copies or moves a node for a user comes here, so the guards
and the name-conflict rules exist once. A refusal the caller can explain is a
:class:`~.refusals.Refused`; anything else that escapes is a failure nobody
planned for.
"""

from collections import namedtuple
from enum import StrEnum

from django.db import transaction

from ..models import File, canonical_name
from ..queries import FilePermission, get_permission
from .announcements import announce_replaced
from .content_writes import move_content_from, replace_content_from
from .files import FileService, GroupRootFolder, new_owner_on_move
from .names import NameTaken, find_node_conflict
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


def _require_replaceable(user, existing, source):
    if not source.content:
        raise Refused(Refusal.NO_CONTENT)
    permission = get_permission(user, existing)
    if permission is None or permission < FilePermission.WRITE:
        raise Refused(Refusal.NOT_REPLACEABLE)
    require_unlocked(user, existing)


def copy_item(
    user,
    node,
    parent,
    on_conflict=OnConflict.RENAME,
    *,
    name=None,
    checkpoint=None,
):
    """Copy *node* under *parent* (None for the user's root), named *name*.

    *name* defaults to the node's own. Only a file can replace, skip or be
    refused on a taken name; a folder or a shortcut is always stored under a
    free one. *checkpoint* is handed to ``FileService.copy``.
    """
    require_action("copy", user, node)
    _require_target(user, node, parent)
    wanted = canonical_name(name) if name is not None else node.name

    if on_conflict != OnConflict.RENAME and node.node_type == File.NodeType.FILE:
        existing = FileService.find_name_conflict(
            user, parent, wanted, exclude_pk=node.pk
        )
        if existing is not None:
            if on_conflict == OnConflict.SKIP:
                return TransferResult(Outcome.SKIPPED, None)
            if on_conflict == OnConflict.ERROR:
                raise Refused(Refusal.NAME_TAKEN)
            _require_replaceable(user, existing, node)
            with refusals():
                replace_content_from(existing, node, acting_user=user)
            announce_replaced(existing, user)
            return TransferResult(Outcome.REPLACED, existing)

    with refusals():
        copied = FileService.copy(
            node, parent, user, name=wanted, acting_user=user, checkpoint=checkpoint
        )
    # A duplicate made next to its source is named "(Copy)" by design: that
    # is not a conflict the user has to hear about.
    same_folder = node.parent_id == (parent.pk if parent is not None else None)
    renamed = copied.name != wanted and not same_folder
    return TransferResult(Outcome.RENAMED if renamed else Outcome.DONE, copied)


def move_onto(user, node, existing):
    """Move the file *node* onto the file *existing*, which takes its bytes.

    The file in place keeps its row - shares, tags, comments, history - and
    the moved one is gone: its content now lives on in *existing*. What a
    move replacing a same-name file does, and what an editor saving by
    renaming a temporary file over a document does.
    """
    _move_onto(user, node, existing)
    announce_replaced(existing, user)
    return TransferResult(Outcome.REPLACED, existing)


@transaction.atomic
def _move_onto(user, node, existing):
    require_action("cut", user, node)
    require_unlocked(user, node)
    _require_replaceable(user, existing, node)
    with refusals():
        move_content_from(existing, node, acting_user=user)


def move_item(user, node, parent, on_conflict=OnConflict.ERROR, *, name=None):
    """Move *node* under *parent* (None for the user's root), named *name*.

    *name* defaults to the node's own; a move to its own folder under another
    name is a rename. Replacing a same-name file hands it the moved file's
    bytes: the file in place keeps its row, the moved one is gone.
    """
    result = _move(user, node, parent, on_conflict, name)
    if result.outcome == Outcome.REPLACED:
        # Outside the transaction: nobody should be told about a replacement
        # that could still roll back.
        announce_replaced(result.node, user)
    return result


@transaction.atomic
def _move(user, node, parent, on_conflict, name):
    require_action("cut", user, node)
    wanted = canonical_name(name) if name is not None else node.name
    if wanted != node.name:
        require_action("rename", user, node)
    staying = node.parent_id == (parent.pk if parent is not None else None)
    if staying and wanted == node.name:
        return TransferResult(Outcome.SKIPPED, None)
    if not staying:
        _require_target(user, node, parent)
        try:
            FileService.validate_move_target(node, parent, user=user)
        except GroupRootFolder as error:
            raise Refused(Refusal.GROUP_ROOT) from error
        except ValueError as error:
            raise Refused(Refusal.TARGET) from error
    require_unlocked(user, node)

    # The names it competes with are those of whoever owns it once moved.
    owner = new_owner_on_move(node, parent, user) or node.owner

    outcome = Outcome.DONE
    if node.node_type == File.NodeType.FILE:
        conflict = FileService.find_name_conflict(
            owner, parent, wanted, exclude_pk=node.pk
        )
    else:
        conflict = find_node_conflict(owner, parent, wanted, exclude_pk=node.pk)

    if conflict is not None:
        if on_conflict == OnConflict.SKIP:
            return TransferResult(Outcome.SKIPPED, None)
        replacing = (
            on_conflict == OnConflict.REPLACE
            and node.node_type == File.NodeType.FILE
            and conflict.node_type == File.NodeType.FILE
        )
        if replacing:
            _move_onto(user, node, conflict)
            return TransferResult(Outcome.REPLACED, conflict)
        if on_conflict != OnConflict.RENAME:
            # Chained so a caller can tell a folder holding the name from a file.
            raise Refused(Refusal.NAME_TAKEN) from NameTaken(
                held_by_folder=conflict.node_type == File.NodeType.FOLDER
            )
        # Renamed before it leaves, so the name has to be free in the folder
        # it is still in as well.
        wanted = FileService.available_file_name(
            owner, parent, wanted, avoiding=(node.parent,)
        )
        outcome = Outcome.RENAMED

    try:
        with refusals():
            _relocate(user, node, parent, wanted, staying)
    except Refused as refusal:
        # A name taken between the look and the move.
        if refusal.reason == Refusal.NAME_TAKEN and on_conflict == OnConflict.SKIP:
            return TransferResult(Outcome.SKIPPED, None)
        raise
    return TransferResult(outcome, node)


def _relocate(user, node, parent, name, staying):
    """Give *node* its new *name* and *parent*, in an order that cannot collide.

    Renaming first collides with a sibling in the current folder that holds
    the new name; moving first collides with a node of the destination
    holding the current one. Renaming first is the default, and the
    destination was checked for the new name already.
    """
    if staying:
        FileService.rename(node, name, acting_user=user)
        return
    # The rename moves the blob outside any transaction, so whatever would
    # refuse the move must refuse before it.
    FileService.check_move_allowed(node, parent, acting_user=user)
    renaming = name != node.name
    rename_first = not (
        renaming
        and find_node_conflict(node.owner, node.parent, name, exclude_pk=node.pk)
    )
    if renaming and rename_first:
        FileService.rename(node, name, acting_user=user)
    FileService.move(node, parent, acting_user=user)
    if renaming and not rename_first:
        FileService.rename(node, name, acting_user=user)
