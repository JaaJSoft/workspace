"""Shortcuts: tree nodes that open another node.

A shortcut is a ``File`` row of ``node_type`` SHORTCUT whose
``shortcut_target`` names the file or folder it opens. In its own folder it is
a node like any other - renamed, moved, copied, trashed, tagged and
favorited as itself - but it has no content, and the server never follows it:
the client opens the target by the target's own uuid, so every endpoint
answers under the viewer's own permission on the target. A shortcut grants
nothing; a group member who finds a shortcut to someone's private file finds a
dead entry.

What a viewer is told about the target follows from the same permission
(:func:`resolve_shortcuts`): the target itself when they can reach it, that
it sits in the trash when they could restore it from there, and nothing at
all otherwise - not its name, not its uuid.
"""

from dataclasses import dataclass
from enum import StrEnum

from ..actions.base import UNRESOLVED
from ..models import File, FileEvent, canonical_name
from ..queries import (
    FilePermission,
    get_permission,
    get_permissions_bulk,
    trash_permissions_bulk,
)
from . import names as _name_helpers
from .events import record_event
from .files import FileService, GroupMismatch
from .scanning.policy import with_scan
from .storage_ops import unique_copy_name


class ShortcutState(StrEnum):
    OK = "ok"
    TRASHED = "trashed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ShortcutResolution:
    """What one viewer gets out of one shortcut.

    ``target`` and ``permission`` are set only when the viewer may know the
    target: ``OK`` and ``TRASHED``.
    """

    state: ShortcutState
    target: File | None = None
    permission: FilePermission | None = None


UNAVAILABLE = ShortcutResolution(ShortcutState.UNAVAILABLE)


class TargetUnreachable(ValueError):
    """The target is unknown to the caller, trashed, or out of their reach.

    One exception for all three, so the answer reveals nothing about a node
    the caller cannot open.
    """


def create_shortcut(
    owner,
    target,
    parent=None,
    *,
    name=None,
    group=None,
    acting_user=None,
    keep_both=False,
):
    """Create a shortcut to *target* under *parent*, owned by *owner*.

    *owner* must be able to open *target*, or ``TargetUnreachable`` is raised;
    whether they may write into *parent* is the caller's check, as for
    ``FileService.create_file``. A shortcut to a shortcut points at the
    latter's target, so a chain never forms. *name* defaults to the name of
    the node the caller picked. A taken name raises ``NameTaken`` unless
    ``keep_both`` asks for the first free ``name (Copy N)``.
    """
    name = canonical_name(name or target.name)
    if target.node_type == File.NodeType.SHORTCUT:
        target = target.shortcut_target
    if target.deleted_at is not None or get_permission(owner, target) is None:
        raise TargetUnreachable()

    group = FileService.node_group(parent, group)
    if parent is None and group is not None:
        # A group node at the root is the group's root folder.
        raise GroupMismatch()

    if not keep_both:
        _name_helpers.check_name_available(owner, parent, name, File.NodeType.SHORTCUT)
    shortcut = File(
        owner=owner,
        name=name,
        node_type=File.NodeType.SHORTCUT,
        parent=parent,
        group=group,
        shortcut_target=target,
    )
    if keep_both:
        _name_helpers.insert_node_keeping_both(shortcut)
    else:
        _name_helpers.insert_node(shortcut)
    record_event(shortcut, acting_user or owner, FileEvent.Action.CREATED)
    return shortcut


def rename_aside(shortcut, *, keep_free=(), acting_user=None):
    """Rename *shortcut* to the first free ``name (Copy N)`` of its folder.

    For the disk sync, which registers a file or a folder written into the
    storage tree by hand under its name on disk or not at all: it matches rows
    to disk entries by name, so a row named otherwise would read as gone from
    the disk on its next run. A shortcut has no name on disk to keep in step,
    so it is the one that gives the name up. *keep_free* lists further names
    the new one must not take - the folder's disk entries, which may have no
    row yet.
    """
    taken = set(
        _name_helpers.sibling_nodes(shortcut.owner, shortcut.parent).values_list(
            "name", flat=True
        )
    )
    taken.update(keep_free)
    name = unique_copy_name(shortcut.name, File.NodeType.SHORTCUT, taken)
    return FileService.rename(shortcut, name, acting_user=acting_user)


def resolve_shortcuts(user, nodes):
    """``{shortcut.pk: ShortcutResolution}`` for the shortcuts among *nodes*.

    One query for the targets and the permission lookups of
    ``get_permissions_bulk`` for the whole batch, whatever its
    size. A trashed target is reported as such only to whoever the trash
    shows it to (``trash_permissions_bulk``): its owner, or a member of its
    group. A share recipient learns nothing more than that it is unavailable.
    """
    shortcuts = [node for node in nodes if node.node_type == File.NodeType.SHORTCUT]
    if not shortcuts:
        return {}

    target_ids = {shortcut.shortcut_target_id for shortcut in shortcuts}
    targets = {
        target.pk: target
        for target in with_scan(File.objects.filter(pk__in=target_ids))
    }
    live = [target for target in targets.values() if target.deleted_at is None]
    trashed = [target for target in targets.values() if target.deleted_at is not None]
    permissions = get_permissions_bulk(user, live)
    permissions.update(trash_permissions_bulk(user, trashed))

    resolutions = {}
    for shortcut in shortcuts:
        target = targets.get(shortcut.shortcut_target_id)
        permission = permissions.get(target.pk) if target is not None else None
        if permission is None:
            resolutions[shortcut.pk] = UNAVAILABLE
        elif target.deleted_at is not None:
            resolutions[shortcut.pk] = ShortcutResolution(
                ShortcutState.TRASHED, target, permission
            )
        else:
            resolutions[shortcut.pk] = ShortcutResolution(
                ShortcutState.OK, target, permission
            )
    return resolutions


def attach_resolutions(user, nodes):
    """Set ``shortcut_resolution`` on every shortcut among *nodes*.

    Where the serializer and the action rules read it, so a listing resolves
    its shortcuts once rather than once per row.
    """
    resolutions = resolve_shortcuts(user, nodes)
    for node in nodes:
        if node.pk in resolutions:
            node.shortcut_resolution = resolutions[node.pk]
    return nodes


def resolution_of(user, shortcut):
    """The resolution attached to *shortcut*, or a fresh one for it alone."""
    resolution = getattr(shortcut, "shortcut_resolution", None)
    if resolution is None:
        resolution = resolve_shortcuts(user, [shortcut]).get(shortcut.pk, UNAVAILABLE)
        shortcut.shortcut_resolution = resolution
    return resolution


class Opens(StrEnum):
    """What opening a shortcut does for its viewer."""

    FOLDER = "folder"
    VIEWER = "viewer"


def opens_as(user, resolution, *, journal_folder=UNRESOLVED):
    """How *user* opens the target of *resolution*: an ``Opens``, or None.

    The target's own open rule decides (``open`` for a folder, ``view`` for a
    file), asked on the target under the viewer's permission on it, so a
    shortcut opens exactly what its viewer could open without it.
    """
    # The registry's action modules import the services package.
    from workspace.files.actions import ActionRegistry

    if resolution.state != ShortcutState.OK:
        return None
    target = resolution.target
    is_folder = target.node_type == File.NodeType.FOLDER
    if not ActionRegistry.is_action_available(
        "open" if is_folder else "view",
        user,
        target,
        permission=resolution.permission,
        journal_folder=journal_folder,
    ):
        return None
    return Opens.FOLDER if is_folder else Opens.VIEWER
