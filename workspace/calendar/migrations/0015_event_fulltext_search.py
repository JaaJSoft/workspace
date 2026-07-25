from django.db import migrations

# Full-text index over event titles, descriptions and locations. Literal
# SQL on purpose: migrations must never import the live declaration
# (regenerate with `manage.py fts_sql` when writing a new one).
# left(description, 100000) on PG: a generated tsvector over ~1MB fails
# the INSERT itself, so an oversized description must be truncated rather
# than break event saving.

PG_FORWARD = """
DROP INDEX IF EXISTS calendar_event_tsv_gin;
ALTER TABLE calendar_event DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE calendar_event ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') ||
    setweight(to_tsvector('simple', f_unaccent(left(coalesce(description, ''), 100000))), 'C') ||
    setweight(to_tsvector('simple', f_unaccent(coalesce(location, ''))), 'B')
  ) STORED;

CREATE INDEX calendar_event_tsv_gin ON calendar_event USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS calendar_event_tsv_gin;
ALTER TABLE calendar_event DROP COLUMN IF EXISTS search_tsv;
"""

SQLITE_FORWARD = """
DROP TRIGGER IF EXISTS calendar_event_fts_ai;
DROP TRIGGER IF EXISTS calendar_event_fts_ad;
DROP TRIGGER IF EXISTS calendar_event_fts_au;
DROP TABLE IF EXISTS calendar_event_fts;

CREATE VIRTUAL TABLE calendar_event_fts USING fts5(
  title, description, location,
  content='calendar_event', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER calendar_event_fts_ai AFTER INSERT ON calendar_event BEGIN
  INSERT INTO calendar_event_fts(rowid, title, description, location)
  VALUES (new.rowid, new.title, new.description, new.location);
END;

CREATE TRIGGER calendar_event_fts_ad AFTER DELETE ON calendar_event BEGIN
  INSERT INTO calendar_event_fts(calendar_event_fts, rowid, title, description, location)
  VALUES ('delete', old.rowid, old.title, old.description, old.location);
END;

CREATE TRIGGER calendar_event_fts_au AFTER UPDATE ON calendar_event BEGIN
  INSERT INTO calendar_event_fts(calendar_event_fts, rowid, title, description, location)
  VALUES ('delete', old.rowid, old.title, old.description, old.location);
  INSERT INTO calendar_event_fts(rowid, title, description, location)
  VALUES (new.rowid, new.title, new.description, new.location);
END;

INSERT INTO calendar_event_fts(calendar_event_fts) VALUES ('rebuild');
INSERT INTO calendar_event_fts(calendar_event_fts, rank)
  VALUES ('rank', 'bm25(10.0, 2.0, 4.0)');
"""

SQLITE_REVERSE = """
DROP TRIGGER IF EXISTS calendar_event_fts_ai;
DROP TRIGGER IF EXISTS calendar_event_fts_ad;
DROP TRIGGER IF EXISTS calendar_event_fts_au;
DROP TABLE IF EXISTS calendar_event_fts;
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
        ("calendar", "0014_event_source"),
        ("common", "0001_f_unaccent"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
