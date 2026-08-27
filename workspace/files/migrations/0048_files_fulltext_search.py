from django.db import migrations

# Full-text index over file names and extracted text content. Literal SQL on
# purpose: migrations must never import the live declaration (regenerate with
# `manage.py fts_sql` when writing a new one).
#
# Unlike the other indexes, this one is written by application code
# (files.index_search_document), not by the database:
#  - PostgreSQL gets a plain tsvector column, not a GENERATED one, because
#    index_document() has to be able to UPDATE it.
#  - SQLite gets a contentless FTS5 table (content=''), so the file text is
#    never persisted; contentless_delete=1 (SQLite >= 3.43) is what lets a row
#    be removed from the index when the file is hard-deleted.
# Neither can be rebuilt from the database alone: existing rows are backfilled
# by `manage.py reindex_files_search`.

PG_FORWARD = """
DROP INDEX IF EXISTS files_file_tsv_gin;
ALTER TABLE files_file DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE files_file ADD COLUMN search_tsv tsvector;

CREATE INDEX files_file_tsv_gin ON files_file USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS files_file_tsv_gin;
ALTER TABLE files_file DROP COLUMN IF EXISTS search_tsv;
"""

SQLITE_FORWARD = """
DROP TABLE IF EXISTS files_file_fts;

CREATE VIRTUAL TABLE files_file_fts USING fts5(
  name, body,
  content='', contentless_delete=1,
  tokenize='unicode61 remove_diacritics 2'
);

INSERT INTO files_file_fts(files_file_fts, rank)
  VALUES ('rank', 'bm25(10.0, 2.0)');
"""

SQLITE_REVERSE = """
DROP TABLE IF EXISTS files_file_fts;
"""


def forward(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)
    elif vendor == "sqlite":
        with schema_editor.connection.cursor() as cursor:
            cursor.executescript(SQLITE_FORWARD)


def reverse(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(PG_REVERSE)
    elif vendor == "sqlite":
        with schema_editor.connection.cursor() as cursor:
            cursor.executescript(SQLITE_REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0047_remove_file_file_group_usage_file_file_group_usage"),
        ("common", "0001_f_unaccent"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
