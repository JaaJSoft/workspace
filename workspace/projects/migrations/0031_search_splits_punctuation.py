from django.db import migrations

# Rebuilds the generated tsvector over f_fts_text, which splits "report.pdf"
# into "report" and "pdf" where the parser kept one token (common 0002).
# Literal SQL on purpose: migrations must never import the live declaration
# (regenerate with `manage.py fts_sql`). SQLite's FTS5 tokenizer already
# splits on punctuation, so there is nothing to do there.

PG_FORWARD = """
DROP INDEX IF EXISTS projects_project_tsv_gin;
ALTER TABLE projects_project DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE projects_project ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_fts_text(coalesce(name, ''))), 'A') ||
    setweight(to_tsvector('simple', f_fts_text(left(coalesce(description, ''), 100000))), 'C')
  ) STORED;

CREATE INDEX projects_project_tsv_gin ON projects_project USING gin (search_tsv);

DROP INDEX IF EXISTS projects_task_tsv_gin;
ALTER TABLE projects_task DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE projects_task ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_fts_text(coalesce(title, ''))), 'A') ||
    setweight(to_tsvector('simple', f_fts_text(left(coalesce(description, ''), 100000))), 'C')
  ) STORED;

CREATE INDEX projects_task_tsv_gin ON projects_task USING gin (search_tsv);
"""

PG_REVERSE = """
DROP INDEX IF EXISTS projects_project_tsv_gin;
ALTER TABLE projects_project DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE projects_project ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_unaccent(coalesce(name, ''))), 'A') ||
    setweight(to_tsvector('simple', f_unaccent(left(coalesce(description, ''), 100000))), 'C')
  ) STORED;

CREATE INDEX projects_project_tsv_gin ON projects_project USING gin (search_tsv);

DROP INDEX IF EXISTS projects_task_tsv_gin;
ALTER TABLE projects_task DROP COLUMN IF EXISTS search_tsv;

ALTER TABLE projects_task ADD COLUMN search_tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') ||
    setweight(to_tsvector('simple', f_unaccent(left(coalesce(description, ''), 100000))), 'C')
  ) STORED;

CREATE INDEX projects_task_tsv_gin ON projects_task USING gin (search_tsv);
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_FORWARD)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(PG_REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0030_backfill_link_event_refs"),
        ("common", "0002_f_fts_text"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
