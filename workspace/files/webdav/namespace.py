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

import posixpath
import re
import unicodedata
from collections import Counter

from workspace.common.text import caseless
from workspace.files.models import File, canonical_name
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


def _same_name(stored, asked):
    """Whether *asked* spells the name *stored*, in whichever Unicode form
    each of them is. Case counts."""
    return canonical_name(stored) == canonical_name(asked)


def named(nodes, name):
    """The node among *nodes* - the members of one folder - that *name*
    spells, or None.

    A name is stored composed or, from before names were composed, as its
    client sent it, and a client asks for it in either form. ``name_key``
    folds both forms, and the case with them: one folder holds one live node
    per key, whose case still has to be the one asked for.
    """
    candidates = nodes.filter(name_key=caseless(name))
    return next((node for node in candidates if _same_name(node.name, name)), None)


def _at(nodes, parts):
    """The node of the tree *nodes* at the tree path *parts*, or None.

    One lookup on ``path`` when its names are all stored in one form. A path
    that mixes them - a folder from before names were composed, holding one
    named since - is under no form of the whole of it: the folder above is
    found the same way, then the node by its name.
    """
    path = "/".join(parts)
    forms = {path, canonical_name(path), unicodedata.normalize("NFD", path)}
    node = nodes.filter(path__in=forms).first()
    if node is not None:
        return node
    parent = None
    if len(parts) > 1:
        parent = _at(nodes, parts[:-1])
        if parent is None:
            return None
    return named(nodes.filter(parent=parent), parts[-1])


def tree_node(user, parts):
    """The node of *user*'s personal or group trees that *parts* names, or None.

    *parts* are the segments of a WebDAV path. None too for the top-level
    folders themselves, which are no node.
    """
    if len(parts) < 2:
        return None
    space, *tree_parts = parts
    if space == PERSONAL:
        return _at(personal_nodes(user), tree_parts)
    if space == GROUPS:
        return _at(group_nodes(user), tree_parts)
    return None


def _shared(user):
    """The files ``/Shared`` holds.

    Those with no place in a tree of *user*'s: a file of their own, or of a
    group they are in, that a share also reaches is served where it lives,
    so that every file has one address.
    """
    return shared_files_qs(user).exclude(full_path_q(user))


# How much of a file's id tells it apart: its end, which is the random part.
_ID_LENGTH = 8
# What every address made by ``_told_apart`` reads like, once folded.
_TOLD_APART = re.compile(r" \(.+ [0-9a-f]{8,}\)(\.[^.]*)?$")


def _told_apart(file_obj, length):
    """*file_obj*'s name with its owner and the last *length* characters of
    its id in it - the whole id for None."""
    stem, extension = posixpath.splitext(file_obj.name)
    tail = file_obj.uuid.hex[-length:] if length else file_obj.uuid.hex
    return f"{stem} ({file_obj.owner.username} {tail}){extension}"


def shared_listing(user):
    """The files shared with *user*, by the name ``/Shared`` lists each under.

    A flat folder of files that come from as many trees, so two of them can
    carry one name. A file is listed under its own name while no other
    shared file carries it, and otherwise under an address made of what is
    its own - ``report (bob 1a2b3c4d).txt``, its owner and the end of its id
    - which no share given or taken back can hand to another file.

    The rule behind both: a name is served only while one file alone wants
    it. Two that do - a file named like the address of another - each fall
    back on their whole id. So a share that changes never passes an address
    from one file to another: a client that kept the old one gets a 404, not
    somebody else's file to write.
    """
    files = list(_shared(user).select_related("owner").order_by("pk"))
    carrying = Counter(file_obj.name_key for file_obj in files)
    wanted = {
        file_obj.pk: (
            file_obj.name
            if carrying[file_obj.name_key] == 1
            else _told_apart(file_obj, _ID_LENGTH)
        )
        for file_obj in files
    }
    wanting = Counter(caseless(name) for name in wanted.values())
    listing = {}
    for file_obj in files:
        name = wanted[file_obj.pk]
        if wanting[caseless(name)] > 1:
            name = _told_apart(file_obj, None)
        listing[name] = file_obj
    return listing


def shared_file(user, name):
    """The file ``/Shared`` lists as *name* for *user*, or None."""
    if _TOLD_APART.search(caseless(name)):
        listing = shared_listing(user)
        return next(
            (
                file_obj
                for listed, file_obj in listing.items()
                if _same_name(listed, name)
            ),
            None,
        )
    # No address made to tell a file apart reads like *name*: it is served
    # for the one shared file that carries it, if there is exactly one. No
    # need to read the whole folder to serve one file of it.
    carrying = list(_shared(user).filter(name_key=caseless(name))[:2])
    if len(carrying) != 1 or not _same_name(carrying[0].name, name):
        return None
    return carrying[0]


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

    The key spells the path composed, whichever form it was asked in: the
    two Unicode forms of an address are one resource, and one lock.
    """
    path = canonical_name(path)
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
