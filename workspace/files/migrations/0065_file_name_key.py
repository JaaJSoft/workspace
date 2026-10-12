"""Add ``File.name_key`` and fill it from every row's name.

Also moves the ``content`` field onto ``TreeStorage``, first, so that any blob
the next migrations copy lands under its row's name exactly.
"""

import unicodedata
from itertools import batched

from django.db import migrations, models

import workspace.files.models

BATCH = 1000


def caseless(text):
    """Inlined from ``common.text``: a migration does not import live code."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return unicodedata.normalize("NFC", decomposed.casefold())


def fill_name_keys(apps, schema_editor):
    File = apps.get_model("files", "File")
    db = schema_editor.connection.alias
    rows = File.objects.using(db).order_by("pk").only("pk", "name")
    last = None
    while True:
        page = rows if last is None else rows.filter(pk__gt=last)
        chunk = list(page[:BATCH])
        if not chunk:
            return
        for row in chunk:
            row.name_key = caseless(row.name)
        for batch in batched(chunk, 500, strict=False):
            File.objects.using(db).bulk_update(batch, ["name_key"])
        last = chunk[-1].pk


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0064_unique_live_names"),
    ]

    operations = [
        migrations.AlterField(
            model_name="file",
            name="content",
            field=models.FileField(
                blank=True,
                max_length=1024,
                null=True,
                storage=workspace.files.models.TreeStorage(allow_overwrite=True),
                upload_to=workspace.files.models.file_upload_path,
            ),
        ),
        migrations.AddField(
            model_name="file",
            name="name_key",
            field=workspace.files.models.CaselessKeyField(
                default="", max_length=1024, source="name"
            ),
            preserve_default=False,
        ),
        migrations.RunPython(fill_name_keys, migrations.RunPython.noop),
    ]
