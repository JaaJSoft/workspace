from django.db import migrations

# Full-text indexes over project names/descriptions and task
# titles/descriptions. Literal SQL on purpose: migrations must never import
# the live declaration (regenerate with `manage.py fts_sql` when writing a
# new one). left(description, 100000) on PG: a generated tsvector over ~1MB
# fails the INSERT itself, so an oversized description must be truncated
# rather than break saving.

PG_FORWARD = """
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

PG_REVERSE = """
DROP INDEX IF EXISTS projects_project_tsv_gin;
ALTER TABLE projects_project DROP COLUMN IF EXISTS search_tsv;
DROP INDEX IF EXISTS projects_task_tsv_gin;
ALTER TABLE projects_task DROP COLUMN IF EXISTS search_tsv;
"""

SQLITE_FORWARD = """
DROP TRIGGER IF EXISTS projects_project_fts_ai;
DROP TRIGGER IF EXISTS projects_project_fts_ad;
DROP TRIGGER IF EXISTS projects_project_fts_au;
DROP TABLE IF EXISTS projects_project_fts;

CREATE VIRTUAL TABLE projects_project_fts USING fts5(
  name, description,
  content='projects_project', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER projects_project_fts_ai AFTER INSERT ON projects_project BEGIN
  INSERT INTO projects_project_fts(rowid, name, description)
  VALUES (new.rowid, new.name, new.description);
END;

CREATE TRIGGER projects_project_fts_ad AFTER DELETE ON projects_project BEGIN
  INSERT INTO projects_project_fts(projects_project_fts, rowid, name, description)
  VALUES ('delete', old.rowid, old.name, old.description);
END;

CREATE TRIGGER projects_project_fts_au AFTER UPDATE ON projects_project BEGIN
  INSERT INTO projects_project_fts(projects_project_fts, rowid, name, description)
  VALUES ('delete', old.rowid, old.name, old.description);
  INSERT INTO projects_project_fts(rowid, name, description)
  VALUES (new.rowid, new.name, new.description);
END;

INSERT INTO projects_project_fts(projects_project_fts) VALUES ('rebuild');
INSERT INTO projects_project_fts(projects_project_fts, rank)
  VALUES ('rank', 'bm25(10.0, 2.0)');

DROP TRIGGER IF EXISTS projects_task_fts_ai;
DROP TRIGGER IF EXISTS projects_task_fts_ad;
DROP TRIGGER IF EXISTS projects_task_fts_au;
DROP TABLE IF EXISTS projects_task_fts;

CREATE VIRTUAL TABLE projects_task_fts USING fts5(
  title, description,
  content='projects_task', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER projects_task_fts_ai AFTER INSERT ON projects_task BEGIN
  INSERT INTO projects_task_fts(rowid, title, description)
  VALUES (new.rowid, new.title, new.description);
END;

CREATE TRIGGER projects_task_fts_ad AFTER DELETE ON projects_task BEGIN
  INSERT INTO projects_task_fts(projects_task_fts, rowid, title, description)
  VALUES ('delete', old.rowid, old.title, old.description);
END;

CREATE TRIGGER projects_task_fts_au AFTER UPDATE ON projects_task BEGIN
  INSERT INTO projects_task_fts(projects_task_fts, rowid, title, description)
  VALUES ('delete', old.rowid, old.title, old.description);
  INSERT INTO projects_task_fts(rowid, title, description)
  VALUES (new.rowid, new.title, new.description);
END;

INSERT INTO projects_task_fts(projects_task_fts) VALUES ('rebuild');
INSERT INTO projects_task_fts(projects_task_fts, rank)
  VALUES ('rank', 'bm25(10.0, 2.0)');
"""

SQLITE_REVERSE = """
DROP TRIGGER IF EXISTS projects_project_fts_ai;
DROP TRIGGER IF EXISTS projects_project_fts_ad;
DROP TRIGGER IF EXISTS projects_project_fts_au;
DROP TABLE IF EXISTS projects_project_fts;
DROP TRIGGER IF EXISTS projects_task_fts_ai;
DROP TRIGGER IF EXISTS projects_task_fts_ad;
DROP TRIGGER IF EXISTS projects_task_fts_au;
DROP TABLE IF EXISTS projects_task_fts;
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
        ("projects", "0002_alter_task_status"),
        ("common", "0001_f_unaccent"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
