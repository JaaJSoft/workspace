"""Move what is already in the trash out of the live tree.

Existing installs kept a trashed node's bytes where they were, under a name
the app considered free - so a file created since may have overwritten them.
This walks every trashed node that is the outermost of its chain and moves
its blobs to ``trash/users/<username>/<uuid>/<name>``
(``trash/groups/<uuid>/...`` for group files), the layout
``services/_trash.py`` maintains from now on.

Where a trashed row and a live row already point at the same blob - the
collision this fixes, after the fact - the live row keeps the file and the
trashed one gets a copy, so neither is left dangling. The bytes are the
live row's either way; the copy exists so the trash still restores to
something rather than to a missing file.
"""

import logging
import os
import posixpath

from django.db import migrations

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def _live_dir(row, username):
    path = row.path or row.name
    if row.group_id:
        return posixpath.join("files", "groups", *path.split("/"))
    return posixpath.join("files", "users", username, *path.split("/"))


def _trash_dir(row, username):
    if row.group_id:
        return posixpath.join("trash", "groups", str(row.uuid))
    return posixpath.join("trash", "users", username, str(row.uuid))


def move_trashed_blobs(apps, schema_editor):
    File = apps.get_model("files", "File")
    db = schema_editor.connection.alias
    storage = File._meta.get_field("content").storage

    trashed = list(
        File.objects.using(db)
        .filter(deleted_at__isnull=False)
        .select_related("owner", "parent")
    )
    if not trashed:
        return

    # Only the outermost trashed node of a chain owns a directory; the rest
    # ride inside it.
    roots = [
        row
        for row in trashed
        if row.parent_id is None or row.parent.deleted_at is None
    ]

    live_paths = set(
        File.objects.using(db)
        .filter(deleted_at__isnull=True, node_type="file")
        .exclude(content="")
        .exclude(content__isnull=True)
        .values_list("content", flat=True)
    )

    for root in roots:
        username = root.owner.username
        destination = posixpath.join(_trash_dir(root, username), root.name)
        if root.node_type == "file":
            _move_file(File, db, storage, root, destination, live_paths)
        else:
            _move_folder(File, db, storage, root, destination, username, live_paths)


def _move_file(File, db, storage, row, destination, live_paths):
    source = (row.content.name or "").replace("\\", "/")
    if not source or source.startswith("trash/"):
        return
    if not _relocate_blob(storage, source, destination, live_paths):
        return
    File.objects.using(db).filter(pk=row.pk).update(content=destination)


def _move_folder(File, db, storage, row, destination, username, live_paths):
    """Move the folder's own blobs, one by one.

    Deliberately not a directory rename. On the legacy layout a live folder
    recreated under the same name resolves to the same directory, so
    renaming it would carry its files into the trash with the ones that
    belong there. For the same reason the subtree is collected through the
    parent chain rather than by path: the path is shared, the subtree is
    not.
    """
    updated = []
    for child, relative in _subtree_files(File, db, row):
        source = (child.content.name or "").replace("\\", "/")
        if not source or source.startswith("trash/"):
            continue
        child_destination = posixpath.join(destination, *relative.split("/"))
        if not _relocate_blob(storage, source, child_destination, live_paths):
            continue
        child.content.name = child_destination
        updated.append(child)

    if updated:
        File.objects.using(db).bulk_update(updated, ["content"], batch_size=500)
    _prune_empty_dirs(storage, _live_dir(row, username))


def _subtree_files(File, db, root):
    """``(row, path relative to root)`` for every file under *root*."""
    found = []
    level = {root.pk: ""}
    while level:
        children = list(File.objects.using(db).filter(parent_id__in=list(level)))
        next_level = {}
        for child in children:
            relative = level[child.parent_id] + child.name
            if child.node_type == "folder":
                next_level[child.pk] = f"{relative}/"
            else:
                found.append((child, relative))
        level = next_level
    return found


def _relocate_blob(storage, source, destination, live_paths):
    """Put the bytes at *destination*; False when nothing could be done.

    A blob a live row has taken over is copied rather than moved, so the
    live row keeps reading and the trashed one stops sharing. A blob that
    is already gone still reports success: the row is repointed either way,
    so no path outlives the layout it belongs to.
    """
    try:
        if not storage.exists(source):
            logger.warning("Trashed blob already missing: %s", scrub(source))
        elif source in live_paths:
            with storage.open(source, "rb") as fh:
                storage.save(destination, fh)
        else:
            _relocate(storage, source, destination)
    except OSError as e:
        logger.error("Could not move trashed blob %s: %s", scrub(source), scrub(e))
        return False
    return True


def _prune_empty_dirs(storage, dir_path):
    """Drop what the moved-out subtree left behind, if anything."""
    try:
        full = storage.path(dir_path)
    except NotImplementedError:
        return
    if not os.path.isdir(full):
        return
    for current, _subdirs, _names in os.walk(full, topdown=False):
        try:
            os.rmdir(current)
        except OSError:
            pass  # still holds a live folder's content


def _relocate(storage, source, destination):
    """Move a blob or a directory, tolerating a source that is not there."""
    try:
        source_full = storage.path(source)
        destination_full = storage.path(destination)
    except NotImplementedError:
        if not storage.exists(source):
            return
        with storage.open(source, "rb") as fh:
            storage.save(destination, fh)
        storage.delete(source)
        return

    if not os.path.exists(source_full):
        return
    os.makedirs(os.path.dirname(destination_full), exist_ok=True)
    os.rename(source_full, destination_full)


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0043_file_lock_token"),
    ]

    operations = [
        migrations.RunPython(move_trashed_blobs, migrations.RunPython.noop),
    ]
