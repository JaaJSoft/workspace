"""Move every blob stored under a rewritten name to the file's own name.

A create used to go through the storage's name cleaning, which stores
``my doc (1).txt`` as ``my_doc_1.txt``; a rename, a move or the trash always
used the name as is. A rewritten name could be another file's name too, and
the disk sync, which matches disk entries to rows by name, took such a blob
for a new file and trashed the row it belonged to.

Each blob moves within its own directory, live or in the trash. One another
row still reads is copied rather than moved, and one whose destination holds
something else is left where it is. Not atomic: a blob that moved cannot
roll back with its row, so each row is written as soon as its bytes moved.
"""

import importlib
import logging
import os
import posixpath

from django.db import migrations

logger = logging.getLogger(__name__)

resolve_0063 = importlib.import_module(
    "workspace.files.migrations.0063_resolve_duplicate_names"
)

BATCH = 1000


def name_blobs_after_their_rows(apps, schema_editor):
    File = apps.get_model("files", "File")
    db = schema_editor.connection.alias
    storage = File._meta.get_field("content").storage
    rows = (
        File.objects.using(db)
        .filter(node_type="file")
        .exclude(content="")
        .exclude(content__isnull=True)
        .order_by("pk")
        .only("pk", "name", "content")
    )
    last = None
    while True:
        page = rows if last is None else rows.filter(pk__gt=last)
        # A page at a time, read before any of it is written: SQLite does not
        # promise a cursor over a table that changes under it.
        chunk = list(page[:BATCH])
        if not chunk:
            return
        for row in chunk:
            _rename_blob(File, db, storage, row)
        last = chunk[-1].pk


def _rename_blob(File, db, storage, row):
    source = row.content.name.replace("\\", "/")
    # A backslash splits the name into directories on the way to storage.
    if "\\" in row.name or posixpath.basename(source) == row.name:
        return
    destination = posixpath.join(posixpath.dirname(source), row.name)
    if _held_by_something_else(storage, source, destination):
        logger.warning(
            "Not renaming %s: %s is taken",
            resolve_0063.scrub(source),
            resolve_0063.scrub(destination),
        )
        return
    content = resolve_0063._relocate(
        File, db, storage, row, destination, exclude={row.pk}
    )
    if content != row.content.name:
        File.objects.using(db).filter(pk=row.pk).update(content=content)


def _held_by_something_else(storage, source, destination):
    if not storage.exists(destination):
        return False
    try:
        return not os.path.samefile(storage.path(source), storage.path(destination))
    except NotImplementedError, OSError:
        return True


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("files", "0067_unique_live_name_keys"),
    ]

    operations = [
        migrations.RunPython(name_blobs_after_their_rows, migrations.RunPython.noop),
    ]
