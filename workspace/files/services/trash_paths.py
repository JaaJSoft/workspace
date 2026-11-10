"""Where a node's bytes live while it sits in the trash.

The storage layout mirrors the tree, and every name-uniqueness predicate
ignores trashed rows - so a trashed node left in place keeps a name that the
app considers free, and the next file to claim it lands on the same path.
Storage allows overwrite, so the trashed bytes would simply be truncated.

Trashing therefore moves the node out of the live tree, into a directory of
its own under ``trash/``:

    trash/users/<username>/<uuid>/report.pdf
    trash/groups/<uuid>/Docs/...

The name stays exactly the one the trash view shows, and a trashed folder
keeps its subtree inside. The uuid level is what makes two deletions of the
same name distinguishable - the trash lists them as two entries, so the disk
has to tell them apart too.

Only the *outermost* trashed node of a chain gets a directory: everything
below it rides inside. That node is its "trash root", and the invariant the
rest of this module maintains is that a node's bytes are under ``trash/`` if
and only if it is one.
"""

import posixpath

from ..models import File

TRASH_ROOT = "trash"


def trash_dir(node):
    """Storage directory that holds *node* while it is trashed."""
    if node.group_id:
        return posixpath.join(TRASH_ROOT, "groups", str(node.uuid))
    return posixpath.join(TRASH_ROOT, "users", node.owner.username, str(node.uuid))


def trash_root_of(node):
    """The trashed ancestor-or-self that owns a directory under ``trash/``.

    ``None`` when *node* is live. Climbs the parent chain by identity rather
    than by ``path``: two trashed folders can carry the same path (trash one,
    make another of the same name, trash that too), and resolving by path
    would hand back the wrong one - and with it the wrong trash directory.
    """
    if node.deleted_at is None:
        return None

    root = node
    parent_id = node.parent_id
    while parent_id:
        parent = File.objects.select_related("owner").filter(pk=parent_id).first()
        if parent is None or parent.deleted_at is None:
            break
        root = parent
        parent_id = parent.parent_id
    return root


def trashed_storage_path(node, root=None):
    """Where *node*'s bytes sit given that it (or an ancestor) is trashed."""
    root = root or trash_root_of(node)
    base = posixpath.join(trash_dir(root), root.name)

    root_path = root.path or root.get_path()
    node_path = node.path or node.get_path()
    if node_path == root_path:
        return base
    relative = node_path[len(root_path) + 1 :]
    return posixpath.join(base, *relative.split("/"))
