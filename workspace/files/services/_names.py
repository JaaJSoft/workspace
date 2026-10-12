"""Internal naming and validation helpers for the file service."""

from contextlib import contextmanager

from django.db import IntegrityError, transaction

from ..models import File
from ._storage_ops import unique_copy_name

# Enough for every upload of a batch to lose the same name once.
_CLAIM_ATTEMPTS = 20


FILE_NAME_TAKEN = "A file with the same name already exists in this folder."
FOLDER_NAME_TAKEN = "A folder with the same name already exists in this folder."


class NameTaken(ValueError):
    """A live node of that folder already holds the name."""

    def __init__(self, held_by_folder=False):
        self.held_by_folder = held_by_folder
        super().__init__(FOLDER_NAME_TAKEN if held_by_folder else FILE_NAME_TAKEN)


def _taken(owner, parent, name):
    conflict = find_node_conflict(owner, parent, name)
    return NameTaken(
        held_by_folder=conflict is not None
        and conflict.node_type == File.NodeType.FOLDER
    )


@contextmanager
def name_claim(owner, parent, name):
    """Run a row write that takes *name*, raising ``NameTaken`` if it is held.

    The block runs in its own savepoint, so a refused write leaves an
    enclosing transaction usable.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError as error:
        if not any(c in str(error) for c in File.LIVE_NAME_CONSTRAINTS):
            raise
        raise _taken(owner, parent, name) from error


def insert_node(node, content=None):
    """INSERT *node*, then put *content* at the path its name derives from.

    The row goes first because it is what reserves the name: the blob path
    derives from the name and the storage overwrites in place, so bytes
    written before a refused INSERT would land on the sibling that holds the
    name. Both commit together, so a failed write takes the row with it.

    Unlike a content replacement, the bytes go straight to their path: a new
    row has no previous version for a staged write to protect.
    """
    with transaction.atomic():
        if content is not None:
            field = File._meta.get_field("content")
            node.path = File._build_path_for(node.name, node.parent_id)
            node.content = field.storage.get_available_name(
                field.generate_filename(node, node.name),
                max_length=field.max_length,
            )
        with name_claim(node.owner, node.parent, node.name):
            node.save()
        if content is not None:
            stored = field.storage.save(node.content.name, content)
            if stored != node.content.name:
                # A storage that renames around a collision moved the blob.
                File.objects.filter(pk=node.pk).update(content=stored)
                node.content = stored
    return node


def insert_node_keeping_both(node, content=None):
    """:func:`insert_node` under *node*'s name, or the first free copy name.

    A name free when it was picked can be taken before the INSERT lands; the
    loser picks again from what the folder holds then.
    """
    requested = node.name
    for _ in range(_CLAIM_ATTEMPTS):
        node.name = available_node_name(
            node.owner, node.parent, requested, node.node_type
        )
        try:
            return insert_node(node, content)
        except NameTaken:
            continue
    raise _taken(node.owner, node.parent, node.name)


def sibling_nodes(owner, parent):
    """Live nodes that share a namespace with a node *owner* puts in *parent*.

    Both node types, because the storage layout mirrors the tree: a file and
    a folder of the same name in the same parent resolve to one path, and so
    do two folders. Names are unique per folder within a group, and per owner
    otherwise.

    At the root the two namespaces are separate directories - ``files/users/
    <username>`` and ``files/groups`` - so a personal node never competes
    with a group root folder the same user happens to own.
    """
    qs = File.objects.filter(parent=parent, deleted_at__isnull=True)
    if parent and parent.group_id:
        return qs.filter(group=parent.group)
    return qs.filter(owner=owner, group__isnull=True)


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
    raise NameTaken(held_by_folder=conflict.node_type == File.NodeType.FOLDER)


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
