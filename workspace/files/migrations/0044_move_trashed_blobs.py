"""Move what is already in the trash out of the live tree.

Existing installs kept a trashed node's bytes where they were, under a name
the app considered free - so a file created since may have overwritten them.
This walks every trashed node that is the outermost of its chain and moves
it to ``trash/users/<username>/<uuid>/<name>`` (``trash/groups/<uuid>/...``
for group files), the layout ``services/_trash.py`` maintains from now on.

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
            _move_folder(File, db, storage, root, destination, username)


def _move_file(File, db, storage, row, destination, live_paths):
    source = (row.content.name or "").replace("\\", "/")
    if not source or source.startswith("trash/"):
        return

    try:
        if not storage.exists(source):
            logger.warning("Trashed blob already missing: %s", scrub(source))
        elif source in live_paths:
            # A live row owns these bytes now. Leave them, hand the trashed
            # row its own copy.
            with storage.open(source, "rb") as fh:
                storage.save(destination, fh)
        else:
            _relocate(storage, source, destination)
    except OSError as e:
        logger.error("Could not move trashed blob %s: %s", scrub(source), scrub(e))
        return

    File.objects.using(db).filter(pk=row.pk).update(content=destination)


def _move_folder(File, db, storage, row, destination, username):
    source = _live_dir(row, username)
    try:
        _relocate(storage, source, destination)
    except OSError as e:
        logger.error("Could not move trashed folder %s: %s", scrub(source), scrub(e))
        return

    # Repoint every blob that rode inside the folder.
    folder_path = row.path or row.name
    descendants = list(
        File.objects.using(db)
        .filter(path__startswith=f"{folder_path}/", node_type="file")
        .exclude(content="")
        .exclude(content__isnull=True)
    )
    updated = []
    for child in descendants:
        name = (child.content.name or "").replace("\\", "/")
        if name.startswith(f"{source}/"):
            child.content.name = destination + name[len(source) :]
            updated.append(child)
    if updated:
        File.objects.using(db).bulk_update(updated, ["content"], batch_size=500)


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
