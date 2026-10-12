"""Rename the live nodes whose names only clash once folded caselessly.

The next migration moves the live name constraints from ``lower(name)`` onto
``name_key``, which also folds non-ASCII case (``lower`` folds ASCII only on
SQLite) and the two encodings of an accent, the way a case-insensitive disk
does. Names that differ only that way were allowed side by side until now.

Resolved as ``0063_resolve_duplicate_names`` resolved its clashes, with its
helpers: the oldest node keeps its name, the others become ``name (Copy N)``,
and their bytes follow them. Not atomic, for the same reason.
"""

import importlib
import logging
import unicodedata

from django.db import migrations
from django.db.models import Count

logger = logging.getLogger(__name__)

resolve_0063 = importlib.import_module(
    "workspace.files.migrations.0063_resolve_duplicate_names"
)


def caseless(text):
    """Inlined from ``common.text``: a migration does not import live code."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return unicodedata.normalize("NFC", decomposed.casefold())


def copy_name(name, node_type, taken):
    counter = 1
    while True:
        suffix = "Copy" if counter == 1 else f"Copy {counter}"
        parts = name.rsplit(".", 1)
        if len(parts) == 2 and node_type == "file":
            candidate = f"{parts[0]} ({suffix}).{parts[1]}"
        else:
            candidate = f"{name} ({suffix})"
        if caseless(candidate) not in taken:
            return candidate
        counter += 1


def resolve_caseless_names(apps, schema_editor):
    File = apps.get_model("files", "File")
    db = schema_editor.connection.alias
    storage = File._meta.get_field("content").storage

    for condition, keys in resolve_0063.SCOPES:
        live = File.objects.using(db).filter(deleted_at__isnull=True, **condition)
        clashes = (
            live.values(*keys, "name_key")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
            .order_by()
        )
        for clash in list(clashes):
            siblings = live.filter(**{key: clash[key] for key in keys})
            holders = list(
                siblings.filter(name_key=clash["name_key"])
                .select_related("owner")
                .order_by("created_at", "pk")
            )
            taken = set(siblings.values_list("name_key", flat=True))
            keeper = holders[0]
            for row in holders[1:]:
                new_name = copy_name(row.name, row.node_type, taken)
                taken.add(caseless(new_name))
                resolve_0063._rename(File, db, storage, row, new_name, keeper)
                File.objects.using(db).filter(pk=row.pk).update(
                    name_key=caseless(new_name)
                )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("files", "0065_file_name_key"),
    ]

    operations = [
        migrations.RunPython(resolve_caseless_names, migrations.RunPython.noop),
    ]
