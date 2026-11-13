from django.db import migrations

# Rebuilds the generated tsvector over f_fts_text, which splits "report.pdf"
# into "report" and "pdf" where the parser kept one token (common 0002).
# Literal SQL on purpose: migrations must never import the live declaration
# (regenerate with `manage.py fts_sql`). SQLite's FTS5 tokenizer already
# splits on punctuation, so there is nothing to do there.

PG_FORWARD = """
DROP INDEX IF EXISTS chat_message_tsv_gin;
ALTER TABLE chat_message DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE chat_message ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_fts_text(left(coalesce(body, ''), 100000))), 'A')
  ) STORED;

CREATE INDEX chat_message_tsv_gin ON chat_message USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS chat_message_tsv_gin;
ALTER TABLE chat_message DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE chat_message ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_unaccent(left(coalesce(body, ''), 100000))), 'A')
  ) STORED;

CREATE INDEX chat_message_tsv_gin ON chat_message USING gin (search_tsv);
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0032_conversationmember_role"),
        ("common", "0002_f_fts_text"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
