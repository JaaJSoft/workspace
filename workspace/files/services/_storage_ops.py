"""Internal storage-path helpers for the file service.

Module-level functions extracted from FileService so the facade in
``files.py`` stays small. Not part of the public API: callers should
go through FileService methods, which delegate here.
"""

import gc
import logging
import os
import posixpath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from workspace.common.logging import scrub

from ..models import File

logger = logging.getLogger(__name__)


def folder_storage_path(folder):
    """Return the storage-relative directory path for *folder*.

    Uses the pre-computed ``folder.path`` to avoid walking the parent
    chain.  Group folders are stored under ``files/groups/<group_name>/...``.
    Personal folders are stored under ``files/users/<username>/...``.
    """
    path = folder.path or folder.get_path()
    if folder.group_id:
        return posixpath.join('files', 'groups', folder.group.name, *path.split('/'))
    return posixpath.join('files', 'users', folder.owner.username, *path.split('/'))


def parent_storage_path(owner, parent):
    """Return the storage-relative directory for *parent* (or user root)."""
    if parent:
        return folder_storage_path(parent)
    return posixpath.join('files', 'users', owner.username)


def ensure_folder_on_storage(folder):
    """Create the folder's directory on the storage backend if supported."""
    storage_path = folder_storage_path(folder)
    try:
        full_path = default_storage.path(storage_path)
        os.makedirs(full_path, exist_ok=True)
    except NotImplementedError:
        logger.debug(
            "Storage backend does not support local filesystem paths; "
            "skipping directory creation for '%s'.",
            scrub(storage_path),
        )


def rename_file_storage(file_obj, new_name):
    """Rename a single file on disk."""
    old_path = file_obj.content.name
    dir_path = posixpath.dirname(old_path)

    _, ext = posixpath.splitext(old_path)
    if '.' not in new_name and ext:
        new_filename = f"{new_name}{ext}"
    else:
        new_filename = new_name
    new_path = posixpath.join(dir_path, new_filename)

    if not default_storage.exists(old_path):
        logger.warning("Old file does not exist: '%s'", scrub(old_path))
        return

    file_handle = None
    try:
        file_handle = default_storage.open(old_path, 'rb')
        content = file_handle.read()
    finally:
        if file_handle:
            file_handle.close()
            gc.collect()  # release handles on Windows

    saved_path = default_storage.save(new_path, ContentFile(content))
    file_obj.content.name = saved_path

    if old_path != saved_path:
        try:
            default_storage.delete(old_path)
        except Exception as e:
            logger.warning("Could not delete old file '%s': %s", scrub(old_path), scrub(e))


def update_descendant_content_names(folder, old_seg, new_seg):
    """Fix ``content.name`` for all descendant files after a rename."""
    folder_path = folder.path or folder.get_path()
    descendants = list(
        File.objects.filter(
            path__startswith=f"{folder_path}/",
            node_type=File.NodeType.FILE,
        ).exclude(content='').exclude(content__isnull=True)
    )

    # Anchor the rename to the folder's actual storage prefix, not the first
    # path segment that happens to match ``old_seg`` -- otherwise nested
    # same-named folders (or a folder whose name collides with an ancestor
    # segment such as the username) would be rewritten in the wrong place.
    old_storage_prefix = folder_storage_path(folder).replace('\\', '/')
    parent_storage = posixpath.dirname(old_storage_prefix)
    new_storage_prefix = posixpath.join(parent_storage, new_seg)

    updated = []
    for child in descendants:
        if not child.content.name:
            continue
        content_name = child.content.name.replace('\\', '/')
        if content_name == old_storage_prefix or content_name.startswith(old_storage_prefix + '/'):
            child.content.name = new_storage_prefix + content_name[len(old_storage_prefix):]
            updated.append(child)

    if updated:
        File.objects.bulk_update(updated, ['content'], batch_size=500)


def rename_folder_storage(folder, old_folder_name, new_folder_name):
    """Rename a folder's directory on storage and update descendant paths."""
    storage_path = folder_storage_path(folder)
    new_storage_path = posixpath.join(posixpath.dirname(storage_path), new_folder_name)

    try:
        old_full = default_storage.path(storage_path)
        new_full = default_storage.path(new_storage_path)
        if os.path.isdir(old_full):
            os.rename(old_full, new_full)
    except NotImplementedError:
        # Some storage backends (for example remote/object storage) do not
        # implement filesystem paths/renames; skip best-effort disk rename.
        pass
    except OSError as e:
        logger.warning("Could not rename folder '%s' -> '%s': %s",
                       scrub(storage_path), scrub(new_storage_path), scrub(e))

    update_descendant_content_names(folder, old_folder_name, new_folder_name)


def move_folder_storage(folder, new_parent, *, new_owner=None):
    """Move a folder directory on storage and update descendant content paths."""
    old_storage_path = folder_storage_path(folder)
    effective_owner = new_owner or folder.owner
    new_parent_storage = parent_storage_path(effective_owner, new_parent)
    new_storage_path = posixpath.join(new_parent_storage, folder.name)

    if old_storage_path == new_storage_path:
        return

    # Move directory on disk
    try:
        old_full = default_storage.path(old_storage_path)
        new_full = default_storage.path(new_storage_path)
        if os.path.isdir(old_full):
            os.makedirs(os.path.dirname(new_full), exist_ok=True)
            os.rename(old_full, new_full)
    except NotImplementedError:
        logger.debug(
            "Storage backend does not provide local filesystem paths; "
            "skipping folder rename '%s' -> '%s'.",
            scrub(old_storage_path),
            scrub(new_storage_path),
        )
    except OSError as e:
        logger.warning("Could not move folder '%s' -> '%s': %s",
                       scrub(old_storage_path), scrub(new_storage_path), scrub(e))

    # Update content.name for all descendant files
    folder_path = folder.path or folder.get_path()
    descendants = list(
        File.objects.filter(
            path__startswith=f"{folder_path}/",
            node_type=File.NodeType.FILE,
        ).exclude(content='').exclude(content__isnull=True)
    )

    old_prefix = old_storage_path.replace('\\', '/')
    new_prefix = new_storage_path.replace('\\', '/')

    updated = []
    for child in descendants:
        if not child.content.name:
            continue
        content_name = child.content.name.replace('\\', '/')
        if content_name.startswith(old_prefix + '/'):
            child.content.name = new_prefix + content_name[len(old_prefix):]
            updated.append(child)

    if updated:
        File.objects.bulk_update(updated, ['content'], batch_size=500)


def move_file_storage(file_obj, new_parent, *, new_owner=None):
    """Move a single file on storage to a new parent directory."""
    old_path = file_obj.content.name
    effective_owner = new_owner or file_obj.owner
    new_parent_storage = parent_storage_path(effective_owner, new_parent)
    new_path = posixpath.join(new_parent_storage, posixpath.basename(old_path))

    if old_path == new_path:
        return

    try:
        old_full = default_storage.path(old_path)
        new_full = default_storage.path(new_path)
        if os.path.isfile(old_full):
            os.makedirs(os.path.dirname(new_full), exist_ok=True)
            os.rename(old_full, new_full)
            file_obj.content.name = new_path
    except NotImplementedError:
        # Fallback for non-local storage backends
        if not default_storage.exists(old_path):
            logger.warning("File does not exist on storage: '%s'", scrub(old_path))
            return
        file_handle = None
        try:
            file_handle = default_storage.open(old_path, 'rb')
            data = file_handle.read()
        finally:
            if file_handle:
                file_handle.close()
                gc.collect()
        saved_path = default_storage.save(new_path, ContentFile(data))
        file_obj.content.name = saved_path
        if old_path != saved_path:
            try:
                default_storage.delete(old_path)
            except Exception as e:
                logger.warning("Could not delete old file '%s': %s", scrub(old_path), scrub(e))
    except OSError as e:
        logger.warning("Could not move file '%s' -> '%s': %s", scrub(old_path), scrub(new_path), scrub(e))


def unique_copy_name(base_name, node_type, existing_names):
    """Pick a unique name given a set of *existing_names*."""
    if base_name not in existing_names:
        return base_name

    counter = 1
    while True:
        suffix = 'Copy' if counter == 1 else f'Copy {counter}'
        parts = base_name.rsplit('.', 1)
        if len(parts) == 2 and node_type == File.NodeType.FILE:
            candidate = f"{parts[0]} ({suffix}).{parts[1]}"
        else:
            candidate = f"{base_name} ({suffix})"

        if candidate not in existing_names:
            return candidate
        counter += 1


def copy_node(node, parent, owner, _sibling_names=None):
    """Recursively copy a single node."""
    if _sibling_names is None:
        _sibling_names = set(
            File.objects.filter(
                owner=owner,
                parent=parent,
                deleted_at__isnull=True,
            ).values_list('name', flat=True)
        )

    new_name = unique_copy_name(
        node.name, node.node_type, _sibling_names,
    )
    _sibling_names.add(new_name)

    copied = File(
        owner=owner,
        name=new_name,
        node_type=node.node_type,
        parent=parent,
        mime_type=node.mime_type,
        icon=node.icon,
        color=node.color,
    )

    if node.node_type == File.NodeType.FILE and node.content:
        try:
            node.content.open('rb')
            data = node.content.read()
        finally:
            node.content.close()
        copied.content = ContentFile(data, name=new_name)
        copied.size = node.size

    copied.save()

    if node.node_type == File.NodeType.FOLDER:
        # The folder was just created - no children yet, so start empty.
        child_names = set()
        for child in File.objects.filter(parent=node, deleted_at__isnull=True):
            copy_node(child, copied, owner, child_names)

    return copied
