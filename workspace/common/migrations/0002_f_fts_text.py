from django.db import migrations

# The text every PostgreSQL full-text index and query is built from. PostgreSQL's
# parser reads "report.pdf", "sunset-beach.jpg" or "a@b.org" as one host, file
# or email token and never as the words in it, so a search for "report" found
# nothing. Turning punctuation into spaces leaves it plain words, the way
# SQLite's unicode61 tokenizer splits them.
PG_FORWARD = """
CREATE OR REPLACE FUNCTION f_fts_text(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT regexp_replace(f_unaccent($1), '[[:punct:]]+', ' ', 'g') $$;
"""

PG_REVERSE = """
DROP FUNCTION IF EXISTS f_fts_text(text);
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0001_f_unaccent"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
