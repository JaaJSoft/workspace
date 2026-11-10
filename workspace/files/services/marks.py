"""Favorites and pins: the marks a user puts on files and folders for themselves.

Both take a batch, are quick, and run within the request. An item the user
may not mark - out of reach, trashed, a file to pin - is left out of the
answer exactly like an item that does not exist.
"""

from django.db.models import Max

from ..models import File, FileFavorite, PinnedFolder
from ..queries import get_permissions_bulk


def set_favorite(user, uuids, favorite):
    """Add the nodes of *uuids* to *user*'s favorites, or take them out.

    Returns the uuids of the nodes that are now as asked.
    """
    nodes = _markable(user, uuids, "toggle_favorite")
    if favorite:
        FileFavorite.objects.bulk_create(
            [FileFavorite(owner=user, file=node) for node in nodes],
            ignore_conflicts=True,
        )
    else:
        FileFavorite.objects.filter(owner=user, file__in=nodes).delete()
    return [node.pk for node in nodes]


def set_pinned(user, uuids, pinned):
    """Pin the folders of *uuids* to *user*'s sidebar, or unpin them.

    New pins go after the existing ones, in the order asked. Returns the uuids
    of the folders that are now as asked.
    """
    folders = _markable(user, uuids, "toggle_pin")
    if pinned:
        last = PinnedFolder.objects.filter(owner=user).aggregate(last=Max("position"))
        start = (last["last"] or 0) + 1
        order = {pk: index for index, pk in enumerate(uuids)}
        folders.sort(key=lambda folder: order[folder.pk])
        PinnedFolder.objects.bulk_create(
            [
                PinnedFolder(owner=user, folder=folder, position=start + index)
                for index, folder in enumerate(folders)
            ],
            ignore_conflicts=True,
        )
    else:
        PinnedFolder.objects.filter(owner=user, folder__in=folders).delete()
    return [folder.pk for folder in folders]


def _markable(user, uuids, action_id):
    """The live nodes of *uuids* on which the registry offers *action_id*."""
    # The registry's action modules import the services package.
    from workspace.files.actions import ActionRegistry

    nodes = list(File.objects.filter(pk__in=uuids, deleted_at__isnull=True))
    permissions = get_permissions_bulk(user, nodes)
    return [
        node
        for node in nodes
        if ActionRegistry.is_action_available(
            action_id, user, node, permission=permissions[node.pk]
        )
    ]
