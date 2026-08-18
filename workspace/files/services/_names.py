"""Internal naming and validation helpers for the file service."""

from ..models import File
from ._storage_ops import unique_copy_name


def sibling_files(owner, parent):
    """Live files that share a namespace with a file *owner* puts in *parent*.

    Names are unique per folder within a group, and per owner otherwise.
    """
    qs = File.objects.filter(
        parent=parent,
        node_type=File.NodeType.FILE,
        deleted_at__isnull=True,
    )
    if parent and parent.group_id:
        return qs.filter(group=parent.group)
    return qs.filter(owner=owner)


def find_name_conflict(owner, parent, name, *, exclude_pk=None):
    """Return the live file already using *name* in that folder, or None."""
    qs = sibling_files(owner, parent).filter(name__iexact=name)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def check_name_available(owner, parent, name, node_type, *, exclude_pk=None):
    """Raise ``ValueError`` if a file with the same name already exists.

    Only enforced for *files* (not folders), case-insensitive, ignoring
    soft-deleted records.  For group folders, uniqueness is scoped to
    the group rather than the owner.
    """
    if node_type != File.NodeType.FILE:
        return
    if find_name_conflict(owner, parent, name, exclude_pk=exclude_pk) is not None:
        raise ValueError("A file with the same name already exists in this folder.")


def available_file_name(owner, parent, name):
    """*name*, or the first free ``name (Copy N).ext`` variant in that folder."""
    taken = set(sibling_files(owner, parent).values_list("name", flat=True))
    return unique_copy_name(name, File.NodeType.FILE, taken)


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
