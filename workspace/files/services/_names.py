"""Internal naming and validation helpers for the file service."""

from ..models import File
from ._storage_ops import unique_copy_name


def sibling_nodes(owner, parent):
    """Live nodes that share a namespace with a node *owner* puts in *parent*.

    Both node types, because the storage layout mirrors the tree: a file and
    a folder of the same name in the same parent resolve to one path, and so
    do two folders. Names are unique per folder within a group, and per owner
    otherwise.
    """
    qs = File.objects.filter(parent=parent, deleted_at__isnull=True)
    if parent and parent.group_id:
        return qs.filter(group=parent.group)
    return qs.filter(owner=owner)


def sibling_files(owner, parent):
    """The file half of :func:`sibling_nodes`.

    Only for the replace-on-conflict flows, which need the row they would be
    overwriting: replacing a folder with a file is not a thing.
    """
    return sibling_nodes(owner, parent).filter(node_type=File.NodeType.FILE)


def find_name_conflict(owner, parent, name, *, exclude_pk=None):
    """Return the live *file* already using *name* in that folder, or None."""
    qs = sibling_files(owner, parent).filter(name__iexact=name)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def find_node_conflict(owner, parent, name, *, exclude_pk=None):
    """Return the live file *or folder* already using *name*, or None."""
    qs = sibling_nodes(owner, parent).filter(name__iexact=name)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def check_name_available(owner, parent, name, node_type, *, exclude_pk=None):
    """Raise ``ValueError`` if the name is taken in that folder.

    Case-insensitive, across both node types, ignoring soft-deleted records
    (a trashed node's bytes are moved out of the tree, so its name is free).
    For group folders, uniqueness is scoped to the group rather than the
    owner.
    """
    conflict = find_node_conflict(owner, parent, name, exclude_pk=exclude_pk)
    if conflict is None:
        return
    if conflict.node_type == File.NodeType.FOLDER:
        raise ValueError("A folder with the same name already exists in this folder.")
    raise ValueError("A file with the same name already exists in this folder.")


def available_node_name(owner, parent, name, node_type, *, avoiding=()):
    """*name*, or the first free ``name (Copy N)`` variant in that folder.

    *avoiding* lists further folders whose names must not be reused either -
    a file renamed before it moves must fit both its old and its new folder.
    """
    taken = set(sibling_nodes(owner, parent).values_list("name", flat=True))
    for other in avoiding:
        taken.update(sibling_nodes(owner, other).values_list("name", flat=True))
    return unique_copy_name(name, node_type, taken)


def available_file_name(owner, parent, name, *, avoiding=()):
    """:func:`available_node_name` for a file."""
    return available_node_name(
        owner, parent, name, File.NodeType.FILE, avoiding=avoiding
    )


def validate_move_target(file_obj, new_parent, user=None):
    """Raise ``ValueError`` if *new_parent* is an invalid move target."""
    if new_parent is None:
        return

    if file_obj.node_type == File.NodeType.FOLDER:
        if new_parent.pk == file_obj.pk:
            raise ValueError("Cannot move a folder into itself.")
        file_path = file_obj.path or file_obj.get_path()
        parent_path = new_parent.path or new_parent.get_path()
        if parent_path.startswith(f"{file_path}/"):
            raise ValueError("Cannot move a folder into one of its descendants.")

    if new_parent.group_id:
        if not user or not user.groups.filter(id=new_parent.group_id).exists():
            raise ValueError("You are not a member of this group.")
    else:
        effective_user_id = user.id if user else file_obj.owner_id
        if new_parent.owner_id != effective_user_id:
            raise ValueError("Cannot move to a folder owned by another user.")
