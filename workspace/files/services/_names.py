"""Internal naming and validation helpers for the file service."""

import mimetypes

from ..models import File


def infer_mime_type(filename, *, uploaded=None):
    """Infer MIME type with priority: upload metadata > filename > default."""
    if uploaded is not None:
        content_type = getattr(uploaded, 'content_type', None)
        if content_type and content_type != 'application/octet-stream':
            return content_type

    candidate = filename or (getattr(uploaded, 'name', None) if uploaded else None)
    if candidate:
        guessed, _ = mimetypes.guess_type(candidate)
        if guessed:
            return guessed

    if uploaded is not None:
        return getattr(uploaded, 'content_type', None) or 'application/octet-stream'
    return 'application/octet-stream'


def check_name_available(owner, parent, name, node_type, *, exclude_pk=None):
    """Raise ``ValueError`` if a file with the same name already exists.

    Only enforced for *files* (not folders), case-insensitive, ignoring
    soft-deleted records.  For group folders, uniqueness is scoped to
    the group rather than the owner.
    """
    if node_type != File.NodeType.FILE:
        return

    qs = File.objects.filter(
        parent=parent,
        node_type=File.NodeType.FILE,
        name__iexact=name,
        deleted_at__isnull=True,
    )
    if parent and parent.group_id:
        qs = qs.filter(group=parent.group)
    else:
        qs = qs.filter(owner=owner)

    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise ValueError('A file with the same name already exists in this folder.')


def validate_move_target(file_obj, new_parent, user=None):
    """Raise ``ValueError`` if *new_parent* is an invalid move target."""
    if new_parent is None:
        return

    if file_obj.node_type == File.NodeType.FOLDER:
        if new_parent.pk == file_obj.pk:
            raise ValueError('Cannot move a folder into itself.')
        file_path = file_obj.path or file_obj.get_path()
        parent_path = new_parent.path or new_parent.get_path()
        if parent_path.startswith(f"{file_path}/"):
            raise ValueError('Cannot move a folder into one of its descendants.')

    if new_parent.group_id:
        if user and not user.groups.filter(id=new_parent.group_id).exists():
            raise ValueError('You are not a member of this group.')
    else:
        effective_user_id = user.id if user else file_obj.owner_id
        if new_parent.owner_id != effective_user_id:
            raise ValueError('Cannot move to a folder owned by another user.')
