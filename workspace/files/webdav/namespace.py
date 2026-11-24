"""The tree a user sees over WebDAV, and what each of its paths names.

    /My Files/<tree path>    their personal tree
    /Groups/<tree path>      the trees of the groups they are in, each under
                             its root folder
    /Shared/<name>           the files shared with them

One top-level folder per tree, like the storage layout (``storage_layout.py``).
A tree path is only unique inside its tree: a personal folder, the root folder
of a group and a file someone shared can all be ``Team/notes.txt``. So a path
is looked up in the tree its first segment names, and nowhere else.

The three names are in every client's configuration - a mount, a sync pair, a
bookmark. They are not translated, and renaming one breaks those.
"""

import itertools
import posixpath
from collections import defaultdict

from workspace.common.text import caseless
from workspace.files.models import File, name_forms
from workspace.files.queries import (
    full_path_q,
    shared_files_qs,
    user_files_qs,
    user_group_files_qs,
)

PERSONAL = "My Files"
GROUPS = "Groups"
SHARED = "Shared"

# First segment of a user's lock keys, ahead of their id. No folder of the
# layout starts with it, so no user's key sits under a group's or another
# user's.
_USER_KEY = "~"


def served(nodes):
    """*nodes* without the shortcuts.

    A client would take one for an empty file, and every byte it wrote into
    it would land on a node that holds none.
    """
    return nodes.exclude(node_type=File.NodeType.SHORTCUT)


def personal_nodes(user):
    """What ``/My Files`` holds, at any depth."""
    return served(user_files_qs(user))


def group_nodes(user):
    """What ``/Groups`` holds, at any depth."""
    return served(user_group_files_qs(user))


def group_roots(user):
    """The root folders of *user*'s groups: what ``/Groups`` lists."""
    return group_nodes(user).filter(parent__isnull=True, node_type=File.NodeType.FOLDER)


def members(user, folder):
    """What *folder* lists for *user*; None is the root of their personal tree."""
    in_group = folder is not None and folder.group_id
    nodes = group_nodes(user) if in_group else personal_nodes(user)
    return nodes.filter(parent=folder)


def tree_node(user, parts):
    """The node of *user*'s personal or group trees that *parts* names, or None.

    *parts* are the segments of a WebDAV path. None too for the top-level
    folders themselves, which are no node.
    """
    if len(parts) < 2:
        return None
    space, *tree_parts = parts
    if space == PERSONAL:
        nodes = personal_nodes(user)
    elif space == GROUPS:
        nodes = group_nodes(user)
    else:
        return None
    node = nodes.filter(path__in=name_forms("/".join(tree_parts))).first()
    if node is not None:
        return node
    # ``path`` was matched in the two forms of the whole path. A folder named
    # before names were composed, holding a node named since, is under
    # neither: look the last segment up by its parent.
    siblings = nodes.filter(name__in=name_forms(tree_parts[-1]))
    if len(tree_parts) == 1:
        return siblings.filter(parent__isnull=True).first()
    parent = nodes.filter(path__in=name_forms("/".join(tree_parts[:-1]))).first()
    if parent is None:
        return None
    return siblings.filter(parent=parent).first()


def _shared(user):
    """The files ``/Shared`` holds.

    Those with no place in a tree of *user*'s: a file of their own, or of a
    group they are in, that a share also reaches is served where it lives,
    so that every file has one address.
    """
    return shared_files_qs(user).exclude(full_path_q(user))


def shared_listing(user):
    """The files shared with *user*, by the name ``/Shared`` lists each under.

    A flat folder of files that come from as many trees, so two of them can
    carry one name. Those are told apart by their owner - ``report (bob).txt``
    - and none of them keeps the bare name: which one would is a matter of
    order, and a name must not pass from one file to another.
    """
    by_key = defaultdict(list)
    for file_obj in _shared(user).select_related("owner").order_by("pk"):
        by_key[file_obj.name_key].append(file_obj)
    alone = [same[0] for same in by_key.values() if len(same) == 1]
    clashing = [
        file_obj for same in by_key.values() if len(same) > 1 for file_obj in same
    ]

    listing = {file_obj.name: file_obj for file_obj in alone}
    taken = {file_obj.name_key for file_obj in alone}
    for file_obj in clashing:
        name = _told_apart(file_obj, taken)
        listing[name] = file_obj
        taken.add(caseless(name))
    return listing


def _told_apart(file_obj, taken):
    """*file_obj*'s name with its owner in it, free among the *taken* keys."""
    stem, extension = posixpath.splitext(file_obj.name)
    owner = file_obj.owner.username
    for attempt in itertools.count(1):
        label = owner if attempt == 1 else f"{owner} {attempt}"
        name = f"{stem} ({label}){extension}"
        if caseless(name) not in taken:
            return name


def shared_file(user, name):
    """The file ``/Shared`` lists as *name* for *user*, or None."""
    forms = name_forms(name)
    # The only shared file of that name is listed under it: no need to read
    # the whole folder to serve one file of it.
    same_name = list(_shared(user).filter(name_key=caseless(name))[:2])
    if len(same_name) == 1:
        return same_name[0] if same_name[0].name in forms else None
    listing = shared_listing(user)
    return next((listing[form] for form in forms if form in listing), None)


def node_at(user, parts):
    """The node *parts* names for *user* in any of the three trees, or None."""
    if parts[:1] == [SHARED]:
        return shared_file(user, parts[1]) if len(parts) == 2 else None
    return tree_node(user, parts)


def _in_group_tree(path):
    parts = [part for part in path.split("/") if part]
    return len(parts) >= 2 and parts[0] == GROUPS


def lock_key(user, path):
    """What the DAV lock table files *path* under, as *user* is served it.

    wsgidav keys a lock by the address it was taken on, and reads a lock as
    covering every key below its own. One address is as many resources as
    there are users - ``/My Files/notes.txt`` is each user's own file - so
    the key carries the user: a lock binds its holder's other clients and
    nobody else's file of that path.

    A group's tree is the one place where an address is the same resource
    for everyone it is served to. Its key is the path alone, so a lock taken
    there binds the group's other members. The folders above it are each
    user's own, ``/Groups`` included: a lock on one of them stops at its
    holder's own trees, and never freezes a group.
    """
    if _in_group_tree(path):
        return path
    return f"/{_USER_KEY}{user.pk}{path}"


def lock_key_path(key):
    """The path :func:`lock_key` made *key* from."""
    first, _, rest = key.lstrip("/").partition("/")
    if first.startswith(_USER_KEY):
        return f"/{rest}"
    return key


def personal_dav_path(tree_path):
    """The WebDAV path of *tree_path* in its owner's personal tree."""
    return posixpath.join("/", PERSONAL, tree_path)


def dav_path(node):
    """Where *node* sits in the WebDAV tree of someone whose trees hold it:
    its owner for a personal node, a member of its group otherwise."""
    if node.group_id:
        return posixpath.join("/", GROUPS, node.get_path())
    return personal_dav_path(node.get_path())
