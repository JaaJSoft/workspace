from django.db import migrations

# f_unaccent is shared by every full-text index (mail, chat, ...). The
# definition matches mail 0025 exactly; CREATE OR REPLACE + IF NOT EXISTS
# keep this idempotent on databases where 0025 already created it.
PG_FORWARD = """
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT unaccent('unaccent', $1) $$;
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)


def reverse(apps, schema_editor):
    # Deliberate no-op: mail 0025 also owns this function; dropping it on an
    # unrelated rollback would break the mail index.
    pass


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunPython(forward, reverse),
    ]
