from django.db import migrations

# Full-text index over chat message bodies. Literal SQL on purpose:
# migrations must never import the live declaration (regenerate with
# `manage.py fts_sql` when writing a new one). left(body, 100000) on PG:
# a generated tsvector over ~1MB fails the INSERT itself, so an oversized
# pasted message must be truncated rather than break message sending.

PG_FORWARD = """
DROP INDEX IF EXISTS chat_message_tsv_gin;
ALTER TABLE chat_message DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE chat_message ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_unaccent(left(coalesce(body, ''), 100000))), 'A')
  ) STORED;

CREATE INDEX chat_message_tsv_gin ON chat_message USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS chat_message_tsv_gin;
ALTER TABLE chat_message DROP COLUMN IF EXISTS search_tsv;
"""

SQLITE_FORWARD = """
DROP TRIGGER IF EXISTS chat_message_fts_ai;
DROP TRIGGER IF EXISTS chat_message_fts_ad;
DROP TRIGGER IF EXISTS chat_message_fts_au;
DROP TABLE IF EXISTS chat_message_fts;

CREATE VIRTUAL TABLE chat_message_fts USING fts5(
  body,
  content='chat_message', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER chat_message_fts_ai AFTER INSERT ON chat_message BEGIN
  INSERT INTO chat_message_fts(rowid, body)
  VALUES (new.rowid, new.body);
END;

CREATE TRIGGER chat_message_fts_ad AFTER DELETE ON chat_message BEGIN
  INSERT INTO chat_message_fts(chat_message_fts, rowid, body)
  VALUES ('delete', old.rowid, old.body);
END;

CREATE TRIGGER chat_message_fts_au AFTER UPDATE ON chat_message BEGIN
  INSERT INTO chat_message_fts(chat_message_fts, rowid, body)
  VALUES ('delete', old.rowid, old.body);
  INSERT INTO chat_message_fts(rowid, body)
  VALUES (new.rowid, new.body);
END;

INSERT INTO chat_message_fts(chat_message_fts) VALUES ('rebuild');
INSERT INTO chat_message_fts(chat_message_fts, rank)
  VALUES ('rank', 'bm25(10.0)');
"""

SQLITE_REVERSE = """
DROP TRIGGER IF EXISTS chat_message_fts_ai;
DROP TRIGGER IF EXISTS chat_message_fts_ad;
DROP TRIGGER IF EXISTS chat_message_fts_au;
DROP TABLE IF EXISTS chat_message_fts;
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
        ("chat", "0020_message_kind_callsession_callparticipant_and_more"),
        ("common", "0001_f_unaccent"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
