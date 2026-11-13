from django.db import migrations

# Rebuilds the generated tsvector over f_fts_text, which splits "report.pdf"
# into "report" and "pdf" where the parser kept one token (common 0002).
# Literal SQL on purpose: migrations must never import the live declaration
# (regenerate with `manage.py fts_sql`). SQLite's FTS5 tokenizer already
# splits on punctuation, so there is nothing to do there.

PG_FORWARD = """
DROP INDEX IF EXISTS mail_mailmessage_tsv_gin;
ALTER TABLE mail_mailmessage DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE mail_mailmessage ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_fts_text(coalesce(subject, ''))), 'A') ||
    setweight(to_tsvector('simple', f_fts_text(coalesce(snippet, ''))), 'C') ||
    setweight(to_tsvector('simple', f_fts_text(coalesce(from_email, ''))), 'B') ||
    setweight(to_tsvector('simple', f_fts_text(coalesce(from_name, ''))), 'B') ||
    setweight(to_tsvector('simple', f_fts_text(left(coalesce(body_text, ''), 100000))), 'D')
  ) STORED;

CREATE INDEX mail_mailmessage_tsv_gin ON mail_mailmessage USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS mail_mailmessage_tsv_gin;
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

CREATE INDEX mail_mailmessage_tsv_gin ON mail_mailmessage USING gin (search_tsv);
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0035_mailfolder_alias_of"),
        ("common", "0002_f_fts_text"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
