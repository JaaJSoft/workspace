"""Repair files whose stored type was a generic content label (txt/unknown/
empty) when their extension reveals a more specific text type.

Magika classifies a sparse Markdown file (e.g. just a heading) as ``txt``;
before ``refine_with_name`` was wired into file creation, that generic label was
stored as-is, so such notes disappeared from views filtering on
``type=markdown`` (the notes list, the ``[[`` search). This relabels them by
filename only -- no file content is read.
"""
from django.db import DEFAULT_DB_ALIAS, migrations
from django.db.models import Q


def reverse_noop(apps, schema_editor):
    pass


def relabel_generic_text_types(File, using=DEFAULT_DB_ALIAS):
    """Relabel rows whose generic ``type`` is contradicted by their extension.

    Pure: works on the stored ``type`` + ``name`` strings, never opens a file.
    Returns the number of rows updated.

    *using* is the connection being migrated. Without it the ORM would read
    and write the default database, which is a different one whenever the
    target is not the default.
    """
    from workspace.files.services.detection import get_label_info, refine_with_name

    to_update = []
    qs = File.objects.using(using).filter(Q(type="txt") | Q(type="unknown") | Q(type="empty"))
    for f in qs.iterator():
        new_label = refine_with_name(f.type, f.name)
        if new_label != f.type:
            f.type = new_label
            f.category = get_label_info(new_label).get("group") or f.category
            to_update.append(f)
    if to_update:
        File.objects.using(using).bulk_update(to_update, ["type", "category"])
    return len(to_update)


def forward(apps, schema_editor):
    relabel_generic_text_types(
        apps.get_model("files", "File"), schema_editor.connection.alias
    )


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0034_remove_file_file_owner_del_type_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, reverse_noop),
    ]
