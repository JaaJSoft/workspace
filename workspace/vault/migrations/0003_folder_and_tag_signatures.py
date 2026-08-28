"""Signature columns for folders and tags.

No backfill, and none is possible: a signature can only be made by the account
that holds the key, so the server has nothing to write. The one-off default of
"" would fail the check constraint added below - which is the intended outcome,
because it can only happen on a database that already holds a folder or a tag,
and none can exist. Both models landed behind the preview flag with no write
path, and this is the migration that gives them one.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vault", "0002_typedentry_loginentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="vaultfolder",
            name="metadata_sig",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="vaulttag",
            name="metadata_sig",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="vaultfolder",
            constraint=models.CheckConstraint(
                condition=models.Q(("metadata_sig", ""), _negated=True),
                name="folder_metadata_sig_not_empty",
            ),
        ),
        migrations.AddConstraint(
            model_name="vaulttag",
            constraint=models.CheckConstraint(
                condition=models.Q(("metadata_sig", ""), _negated=True),
                name="tag_metadata_sig_not_empty",
            ),
        ),
    ]
