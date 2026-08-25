"""Internal storage-path helpers for the file service.

Module-level functions extracted from FileService so the facade in
``files.py`` stays small. Not part of the public API: callers should
go through FileService methods, which delegate here.
"""

import gc
import logging
import os
import posixpath
import shutil

from django.core.files.base import ContentFile
from django.core.files.base import File as DjangoFile
from django.core.files.storage import default_storage

from workspace.common.logging import scrub

from ..models import File
from . import _trash
from .content_hash import hash_stream

logger = logging.getLogger(__name__)


def folder_storage_path(folder):
    """Return the storage-relative directory path for *folder*.

    A trashed folder lives under ``trash/`` instead of the live tree; see
    ``_trash`` for why and for the layout.
    """
    if folder.deleted_at is not None:
        return _trash.trashed_storage_path(folder)
    return live_folder_path(folder)


def live_folder_path(folder):
    """Where *folder* sits in the live tree, whatever its trash state.

    Uses the pre-computed ``folder.path`` to avoid walking the parent
    chain.  Group folders are stored under ``files/groups/<group_name>/...``.
    Personal folders are stored under ``files/users/<username>/...``.
    """
    path = folder.path or folder.get_path()
    if folder.group_id:
        return posixpath.join("files", "groups", *path.split("/"))
    return posixpath.join("files", "users", folder.owner.username, *path.split("/"))


def parent_storage_path(owner, parent):
    """Return the storage-relative directory for *parent* (or user root)."""
    if parent:
        return folder_storage_path(parent)
    return posixpath.join("files", "users", owner.username)


def current_node_path(node):
    """Where *node*'s bytes sit right now, according to the database."""
    if node.node_type == File.NodeType.FOLDER:
        return folder_storage_path(node)
    return node.content.name if node.content else None


def live_node_path(node):
    """Where *node* belongs in the live tree, whatever its trash state."""
    if node.node_type == File.NodeType.FOLDER:
        return live_folder_path(node)
    return posixpath.join(live_parent_path(node), node.name)


def live_parent_path(node):
    """The live directory that should contain *node*."""
    if node.parent_id:
        return live_folder_path(node.parent)
    if node.group_id:
        return posixpath.join("files", "groups")
    return posixpath.join("files", "users", node.owner.username)


def trash_node_path(node):
    """Where *node*'s bytes go once it is trashed in its own right."""
    return posixpath.join(_trash.trash_dir(node), node.name)


def reconcile_trashed_children(node):
    """Move out whatever is still trashed inside a node that just came back.

    Restoring a file also restores its ancestors, so the directory that came
    back out of the trash can still hold trashed siblings. Each of those is
    now the outermost trashed node of its chain, so it needs a trash
    directory of its own.
    """
    if node.node_type != File.NodeType.FOLDER:
        return
    path = node.path or node.get_path()
    still_trashed = (
        File.objects.filter(path__startswith=f"{path}/", deleted_at__isnull=False)
        .select_related("owner", "parent")
        .order_by("path")
    )
    for child in still_trashed:
        if child.parent is not None and child.parent.deleted_at is not None:
            continue  # rides inside its own trashed ancestor
        source = (
            child.content.name
            if child.node_type == File.NodeType.FILE
            else live_folder_path(child)
        )
        move_node_storage(child, source, trash_node_path(child))


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


def delete_node_storage(node):
    """Best-effort disk cleanup for a File row about to be deleted.

    Runs from the ``pre_delete`` signal, so it covers instance, queryset and
    cascade deletes alike. Paths are resolved through the same helpers as the
    other storage ops (``folder_storage_path``), never rebuilt by hand.
    """
    if node.node_type == File.NodeType.FILE:
        _delete_file_storage(node)
    else:
        _delete_folder_storage(node)


def _delete_file_storage(node):
    if node.has_thumbnail:
        from .thumbnails.generation import delete_thumbnail

        delete_thumbnail(node.uuid)

    if not node.content:
        return
    file_path = node.content.name
    try:
        if file_path and default_storage.exists(file_path):
            default_storage.delete(file_path)
            logger.info("Deleted physical file: %s", scrub(file_path))
            try:
                _remove_empty_parents(posixpath.dirname(file_path))
            except OSError as e:
                logger.warning(
                    "Could not remove empty directory for %s: %s",
                    scrub(file_path),
                    scrub(e),
                )
    except Exception as e:
        logger.error("Error deleting physical file %s: %s", scrub(file_path), scrub(e))


# The two storage roots the cleanup climb must never remove.
_STORAGE_ROOTS = ("files", _trash.TRASH_ROOT)


def _remove_empty_parents(dir_path):
    """Climb from *dir_path* removing directories as long as they are empty."""
    while dir_path and dir_path not in _STORAGE_ROOTS:
        full_path = default_storage.path(dir_path)
        if not os.path.isdir(full_path) or os.listdir(full_path):
            break
        os.rmdir(full_path)
        logger.info("Deleted empty directory: %s", scrub(dir_path))
        dir_path = posixpath.dirname(dir_path)


def _delete_folder_storage(node):
    try:
        storage_path = folder_storage_path(node)
        # Fail closed on '.'/'..' components: they resolve to an ancestor
        # directory, and rmtree there would take unrelated data with it.
        # File.save() rejects such names, but a legacy or hand-edited row
        # must not be able to widen the blast radius.
        if any(part in (".", "..") for part in storage_path.split("/")):
            logger.warning(
                "Refusing to remove folder directory with unsafe path: %s",
                scrub(storage_path),
            )
            return
        full_path = default_storage.path(storage_path)
        # rmtree, not rmdir: descendant rows are deleted before the folder in
        # the cascade, but the directory may still hold orphaned entries the
        # DB never knew about.
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
            logger.info("Deleted folder and contents: %s", scrub(storage_path))
    except NotImplementedError:
        logger.debug(
            "Storage backend does not support local filesystem paths; "
            "skipping directory cleanup for folder '%s'.",
            scrub(node.name),
        )
    except Exception as e:
        logger.warning("Could not delete folder %s: %s", scrub(node.name), scrub(e))


def rename_file_storage(file_obj, new_name):
    """Rename a single file on disk."""
    old_path = file_obj.content.name
    dir_path = posixpath.dirname(old_path)

    _, ext = posixpath.splitext(old_path)
    if "." not in new_name and ext:
        new_filename = f"{new_name}{ext}"
    else:
        new_filename = new_name
    new_path = posixpath.join(dir_path, new_filename)

    if not default_storage.exists(old_path):
        logger.warning("Old file does not exist: '%s'", scrub(old_path))
        return

    file_handle = None
    try:
        file_handle = default_storage.open(old_path, "rb")
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
            logger.warning(
                "Could not delete old file '%s': %s", scrub(old_path), scrub(e)
            )


def update_descendant_content_names(folder, old_seg, new_seg):
    """Fix ``content.name`` for all descendant files after a rename."""
    folder_path = folder.path or folder.get_path()
    descendants = list(
        File.objects.filter(
            path__startswith=f"{folder_path}/",
            node_type=File.NodeType.FILE,
        )
        .exclude(content="")
        .exclude(content__isnull=True)
    )

    # Anchor the rename to the folder's actual storage prefix, not the first
    # path segment that happens to match ``old_seg`` -- otherwise nested
    # same-named folders (or a folder whose name collides with an ancestor
    # segment such as the username) would be rewritten in the wrong place.
    old_storage_prefix = folder_storage_path(folder).replace("\\", "/")
    parent_storage = posixpath.dirname(old_storage_prefix)
    new_storage_prefix = posixpath.join(parent_storage, new_seg)

    updated = []
    for child in descendants:
        if not child.content.name:
            continue
        content_name = child.content.name.replace("\\", "/")
        if content_name == old_storage_prefix or content_name.startswith(
            old_storage_prefix + "/"
        ):
            child.content.name = (
                new_storage_prefix + content_name[len(old_storage_prefix) :]
            )
            updated.append(child)

    if updated:
        File.objects.bulk_update(updated, ["content"], batch_size=500)


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
        logger.warning(
            "Could not rename folder '%s' -> '%s': %s",
            scrub(storage_path),
            scrub(new_storage_path),
            scrub(e),
        )

    update_descendant_content_names(folder, old_folder_name, new_folder_name)


def move_folder_storage(folder, new_parent, *, new_owner=None):
    """Move a folder directory on storage and update descendant content paths."""
    old_storage_path = folder_storage_path(folder)
    effective_owner = new_owner or folder.owner
    new_parent_storage = parent_storage_path(effective_owner, new_parent)
    new_storage_path = posixpath.join(new_parent_storage, folder.name)

    if old_storage_path == new_storage_path:
        return

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
        logger.warning(
            "Could not move folder '%s' -> '%s': %s",
            scrub(old_storage_path),
            scrub(new_storage_path),
            scrub(e),
        )

    # Update content.name for all descendant files
    folder_path = folder.path or folder.get_path()
    descendants = list(
        File.objects.filter(
            path__startswith=f"{folder_path}/",
            node_type=File.NodeType.FILE,
        )
        .exclude(content="")
        .exclude(content__isnull=True)
    )

    old_prefix = old_storage_path.replace("\\", "/")
    new_prefix = new_storage_path.replace("\\", "/")

    updated = []
    for child in descendants:
        if not child.content.name:
            continue
        content_name = child.content.name.replace("\\", "/")
        if content_name.startswith(old_prefix + "/"):
            child.content.name = new_prefix + content_name[len(old_prefix) :]
            updated.append(child)

    if updated:
        File.objects.bulk_update(updated, ["content"], batch_size=500)


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
            file_handle = default_storage.open(old_path, "rb")
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
                logger.warning(
                    "Could not delete old file '%s': %s", scrub(old_path), scrub(e)
                )
    except OSError as e:
        logger.warning(
            "Could not move file '%s' -> '%s': %s",
            scrub(old_path),
            scrub(new_path),
            scrub(e),
        )


def _relocate_on_storage(source, destination, *, expect_dir):
    """Move a blob or a directory on storage.

    Returns False when there is nothing to move: an empty folder whose
    directory was never materialised, or a *source* whose kind on disk
    contradicts the row - a file row pointing at what is now a directory,
    which the disk sync runs into when a path flips from one to the other.
    Moving that directory would drag content the row never owned into the
    trash. Raises ``OSError`` when a move was attempted and failed, so the
    caller's transaction rolls back rather than leaving rows pointing at a
    path the bytes never reached.
    """
    try:
        source_full = default_storage.path(source)
        destination_full = default_storage.path(destination)
    except NotImplementedError:
        return _relocate_without_paths(source, destination)

    if not os.path.exists(source_full):
        return False
    if os.path.isdir(source_full) != expect_dir:
        logger.warning(
            "Not moving '%s': it is a %s, the row says %s",
            scrub(source),
            "directory" if os.path.isdir(source_full) else "file",
            "directory" if expect_dir else "file",
        )
        return False
    os.makedirs(os.path.dirname(destination_full), exist_ok=True)
    os.rename(source_full, destination_full)
    return True


def _relocate_without_paths(source, destination):
    """Fallback for backends with no local filesystem paths (object storage)."""
    if not default_storage.exists(source):
        return False
    handle = None
    try:
        handle = default_storage.open(source, "rb")
        data = handle.read()
    finally:
        if handle:
            handle.close()
            gc.collect()  # release handles on Windows
    saved = default_storage.save(destination, ContentFile(data))
    if saved != destination:
        # The backend picked a different name, so the caller's stored path
        # would be wrong; undo and fail loudly rather than lose the bytes.
        default_storage.delete(saved)
        raise OSError(f"Storage refused the destination path {destination!r}")
    default_storage.delete(source)
    return True


def rewrite_descendant_content_names(folder, old_prefix, new_prefix):
    """Repoint every blob that rides inside *folder* at the new prefix.

    Anchored on the folder's actual storage prefix rather than on a path
    segment, so nested same-named folders (or a folder whose name collides
    with an ancestor segment such as the username) are not rewritten in the
    wrong place.
    """
    folder_path = folder.path or folder.get_path()
    descendants = (
        File.objects.filter(
            path__startswith=f"{folder_path}/",
            node_type=File.NodeType.FILE,
        )
        .exclude(content="")
        .exclude(content__isnull=True)
    )

    old_prefix = old_prefix.replace("\\", "/")
    new_prefix = new_prefix.replace("\\", "/")

    updated = []
    for child in descendants:
        if not child.content.name:
            continue
        content_name = child.content.name.replace("\\", "/")
        if content_name.startswith(f"{old_prefix}/"):
            child.content.name = new_prefix + content_name[len(old_prefix) :]
            updated.append(child)

    if updated:
        File.objects.bulk_update(updated, ["content"], batch_size=500)


def move_node_storage(node, source, destination):
    """Move *node* from *source* to *destination* on storage.

    Rewrites ``content.name`` for the node (or, for a folder, for every blob
    riding inside it) before touching the disk, so the rename is the last
    step: a failure there leaves the surrounding transaction free to roll
    back with nothing moved.
    """
    if source == destination:
        return

    is_folder = node.node_type == File.NodeType.FOLDER
    if is_folder:
        rewrite_descendant_content_names(node, source, destination)
    else:
        if not node.content or not node.content.name:
            return
        node.content.name = destination
        File.objects.filter(pk=node.pk).update(content=destination)

    if _relocate_on_storage(source, destination, expect_dir=is_folder):
        logger.info("Moved %s -> %s", scrub(source), scrub(destination))


def unique_copy_name(base_name, node_type, existing_names):
    """Pick a unique name given a set of *existing_names*.

    Case-insensitive, like the name uniqueness rule it exists to satisfy.
    """
    taken = {name.casefold() for name in existing_names}
    if base_name.casefold() not in taken:
        return base_name

    counter = 1
    while True:
        suffix = "Copy" if counter == 1 else f"Copy {counter}"
        parts = base_name.rsplit(".", 1)
        if len(parts) == 2 and node_type == File.NodeType.FILE:
            candidate = f"{parts[0]} ({suffix}).{parts[1]}"
        else:
            candidate = f"{base_name} ({suffix})"

        if candidate.casefold() not in taken:
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
            ).values_list("name", flat=True)
        )

    new_name = unique_copy_name(
        node.name,
        node.node_type,
        _sibling_names,
    )
    _sibling_names.add(new_name)

    copied = File(
        owner=owner,
        name=new_name,
        node_type=node.node_type,
        parent=parent,
        # The blob's storage root is chosen by file_upload_path from
        # instance.group_id, so the group has to be set before save().
        group=parent.group if parent is not None else None,
        mime_type=node.mime_type,
        type=node.type,
        category=node.category,
        viewer=node.viewer,
        icon=node.icon,
        color=node.color,
    )

    if node.node_type == File.NodeType.FILE and node.content:
        # Stream the source blob into a fresh storage path. Wrapping the
        # opened FieldFile in django.core.files.File flips _committed=False
        # so the destination FileField triggers storage.save(), which copies
        # via content.chunks() (default 64KB) instead of buffering everything
        # in memory. A FieldFile passed directly would be _committed=True and
        # the two rows would silently share the same blob.
        #
        # The try/except is intentionally narrow: only the source-open step
        # is what we attribute to "source blob missing". A destination-side
        # OSError raised later from copied.save() (disk full, permission on
        # the destination path, remote storage flake) must propagate
        # without that misleading log line.
        try:
            src = node.content.open("rb")
        except FileNotFoundError, OSError:
            logger.warning(
                "Source blob missing while copying %s",
                scrub(node.content.name),
            )
            raise

        with src:
            copied.content = DjangoFile(src, name=new_name)
            copied.size = node.size
            # Same bytes, same digest; only legacy rows without one need
            # the extra read.
            copied.content_hash = node.content_hash or hash_stream(src)
            copied.save()
    else:
        copied.save()

    if node.node_type == File.NodeType.FOLDER:
        # The folder was just created - no children yet, so start empty.
        child_names = set()
        for child in File.objects.filter(parent=node, deleted_at__isnull=True):
            copy_node(child, copied, owner, child_names)

    return copied
