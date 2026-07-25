from django.db import migrations

PG_FORWARD = """
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT unaccent('unaccent', $1) $$;

ALTER TABLE mail_mailmessage ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    to_tsvector('simple',
      f_unaccent(coalesce(subject, '')    || ' ' ||
                 coalesce(snippet, '')      || ' ' ||
                 coalesce(from_email, '')   || ' ' ||
                 coalesce(from_name, ''))
    )
  ) STORED;

CREATE INDEX mail_message_tsv_gin ON mail_mailmessage USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS mail_message_tsv_gin;
ALTER TABLE mail_mailmessage DROP COLUMN IF EXISTS search_tsv;
DROP FUNCTION IF EXISTS f_unaccent(text);
"""

SQLITE_FORWARD = """
CREATE VIRTUAL TABLE mail_message_fts USING fts5(
  subject, snippet, from_email, from_name,
  content='mail_mailmessage', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER mail_message_fts_ai AFTER INSERT ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(rowid, subject, snippet, from_email, from_name)
  VALUES (new.rowid, new.subject, new.snippet, new.from_email, new.from_name);
END;

CREATE TRIGGER mail_message_fts_ad AFTER DELETE ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(mail_message_fts, rowid, subject, snippet, from_email, from_name)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_email, old.from_name);
END;

CREATE TRIGGER mail_message_fts_au AFTER UPDATE ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(mail_message_fts, rowid, subject, snippet, from_email, from_name)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_email, old.from_name);
  INSERT INTO mail_message_fts(rowid, subject, snippet, from_email, from_name)
  VALUES (new.rowid, new.subject, new.snippet, new.from_email, new.from_name);
END;

INSERT INTO mail_message_fts(mail_message_fts) VALUES ('rebuild');
"""

SQLITE_REVERSE = """
DROP TRIGGER IF EXISTS mail_message_fts_ai;
DROP TRIGGER IF EXISTS mail_message_fts_ad;
DROP TRIGGER IF EXISTS mail_message_fts_au;
DROP TABLE IF EXISTS mail_message_fts;
"""


def forward(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)
    elif vendor == "sqlite":
        # executescript is needed for the multi-statement trigger block.
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
        ("mail", "0024_remove_mailmessage_from_address"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
