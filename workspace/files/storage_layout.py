"""Where the live tree sits on storage.

    files/users/<username>/<tree path>    a user's personal tree
    files/groups/<tree path>              the groups' trees, each under its
                                          root folder

A node's blob or directory is its tree path under the root of its tree, name
as given: nothing else decides where a live node's bytes belong. Trashed
nodes live elsewhere (``services/trash_paths.py``).
"""

import posixpath

FILES_ROOT = "files"
USERS_ROOT = posixpath.join(FILES_ROOT, "users")
GROUPS_ROOT = posixpath.join(FILES_ROOT, "groups")


def user_root(username):
    """The directory a user's personal tree hangs from."""
    return posixpath.join(USERS_ROOT, username)


def tree_root(node):
    """The directory *node*'s tree hangs from: the groups', or its owner's."""
    if node.group_id:
        return GROUPS_ROOT
    return user_root(node.owner.username)


def live_path(node):
    """Where *node* sits in the live tree, whatever its trash state.

    From the denormalized ``path``, which a node's save keeps up to date for
    itself and its subtree, so no parent is read.
    """
    return posixpath.join(tree_root(node), *node.get_path().split("/"))
