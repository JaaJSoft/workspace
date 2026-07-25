from django.db import migrations

# Weighted rebuild of the mail FTS index, adding body_text. Weights keep a
# subject hit ranked above a term buried (or repeated) in a long body:
# subject A/10, from_* B/4, snippet C/2, body D/1 (PG letters and SQLite
# bm25 multipliers express the same ratios).
#
# left(body_text, 100000) on PG: a tsvector is capped at 1MB and a generated
# column that overflows makes the INSERT itself fail, which would break mail
# sync on an oversized message. Truncating the input keeps inserts safe.

PG_FORWARD = """
DROP INDEX IF EXISTS mail_message_tsv_gin;
ALTER TABLE mail_mailmessage DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE mail_mailmessage ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_unaccent(coalesce(subject, ''))), 'A') ||
    setweight(to_tsvector('simple',
      f_unaccent(coalesce(from_email, '') || ' ' || coalesce(from_name, ''))), 'B') ||
    setweight(to_tsvector('simple', f_unaccent(coalesce(snippet, ''))), 'C') ||
    setweight(to_tsvector('simple',
      f_unaccent(left(coalesce(body_text, ''), 100000))), 'D')
  ) STORED;

CREATE INDEX mail_message_tsv_gin ON mail_mailmessage USING gin (search_tsv);
"""

# Restores the 0025 definition (4 unweighted fields, no body).
PG_REVERSE = """
DROP INDEX IF EXISTS mail_message_tsv_gin;
ALTER TABLE mail_mailmessage DROP COLUMN IF EXISTS search_tsv;

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

SQLITE_FORWARD = """
DROP TRIGGER IF EXISTS mail_message_fts_ai;
DROP TRIGGER IF EXISTS mail_message_fts_ad;
DROP TRIGGER IF EXISTS mail_message_fts_au;
DROP TABLE IF EXISTS mail_message_fts;

CREATE VIRTUAL TABLE mail_message_fts USING fts5(
  subject, snippet, from_email, from_name, body_text,
  content='mail_mailmessage', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER mail_message_fts_ai AFTER INSERT ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(rowid, subject, snippet, from_email, from_name, body_text)
  VALUES (new.rowid, new.subject, new.snippet, new.from_email, new.from_name, new.body_text);
END;

CREATE TRIGGER mail_message_fts_ad AFTER DELETE ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(mail_message_fts, rowid, subject, snippet, from_email, from_name, body_text)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_email, old.from_name, old.body_text);
END;

CREATE TRIGGER mail_message_fts_au AFTER UPDATE ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(mail_message_fts, rowid, subject, snippet, from_email, from_name, body_text)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_email, old.from_name, old.body_text);
  INSERT INTO mail_message_fts(rowid, subject, snippet, from_email, from_name, body_text)
  VALUES (new.rowid, new.subject, new.snippet, new.from_email, new.from_name, new.body_text);
END;

INSERT INTO mail_message_fts(mail_message_fts) VALUES ('rebuild');
INSERT INTO mail_message_fts(mail_message_fts, rank)
  VALUES ('rank', 'bm25(10.0, 2.0, 4.0, 4.0, 1.0)');
"""

# Restores the 0025 table and triggers (4 columns, default bm25 rank).
SQLITE_REVERSE = """
DROP TRIGGER IF EXISTS mail_message_fts_ai;
DROP TRIGGER IF EXISTS mail_message_fts_ad;
DROP TRIGGER IF EXISTS mail_message_fts_au;
DROP TABLE IF EXISTS mail_message_fts;

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
        ("mail", "0025_mail_fulltext_search"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
