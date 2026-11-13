from django.db import migrations


def forward(apps, schema_editor):
    """Hand every file back to the search catch-up, on PostgreSQL only.

    The PostgreSQL documents were built before f_fts_text (common 0002) split
    "report.pdf" into its words, and only the application can build them
    again: the text never reaches the database. A file without a state row is
    pending, so the hourly catch-up re-indexes the library; until then a file
    keeps the document it had. SQLite's tokenizer always split on punctuation.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    SearchIndexState = apps.get_model("files", "SearchIndexState")
    SearchIndexState.objects.using(schema_editor.connection.alias).all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0078_storage_usage"),
        ("common", "0002_f_fts_text"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
