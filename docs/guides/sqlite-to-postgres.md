# Migrating from SQLite to PostgreSQL

SQLite is the default database for Workspace and works well for small to medium instances. This guide explains how to migrate an existing instance to PostgreSQL when you need better concurrency or scalability.

## When to migrate

Consider switching to PostgreSQL if you experience:

- **Write contention** under concurrent users (slow responses, "database is locked" errors)
- **Multi-worker deployments** where SQLite's single-writer limitation becomes a bottleneck

If your instance runs fine on SQLite, there is no reason to migrate.

## Prerequisites

- A running PostgreSQL server (14+)
- An empty database created for Workspace
- The `DATABASE_URL` for the target database

```bash
# Create the database (example)
createdb -U postgres workspace
```

Your target URL will look like: `postgres://user:password@localhost:5432/workspace`

## Automatic migration

The `migrate_to_postgres` management command handles the full process:

```bash
python manage.py migrate_to_postgres postgres://user:password@localhost:5432/workspace
```

The command will:

1. Verify that the current database is SQLite and the target is PostgreSQL
2. Apply all Django migrations on the target database
3. Export all data from SQLite
4. Import the data into PostgreSQL
5. Verify record counts match between both databases

### Options

| Flag          | Description                                             |
|---------------|---------------------------------------------------------|
| `--dry-run`   | Run pre-checks and export without writing to the target |
| `--keep-dump` | Keep the intermediate JSON dump file after import       |

### Example with Docker Compose

```bash
# Stop the application
docker compose down

# Run the migration (web container must reach PostgreSQL)
docker compose run --rm web python manage.py migrate_to_postgres postgres://user:password@db:5432/workspace

# Update your .env or docker-compose.yml to set DATABASE_URL
# DATABASE_URL=postgres://user:password@db:5432/workspace

# Restart
docker compose up -d
```

## Manual migration

If you prefer to run each step yourself:

### 1. Export data from SQLite

```bash
python manage.py dumpdata \
  --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission \
  --indent 2 \
  -o dump.json
```

### 2. Apply migrations on PostgreSQL

```bash
DATABASE_URL=postgres://user:password@localhost:5432/workspace \
  python manage.py migrate
```

### 3. Load data into PostgreSQL

```bash
DATABASE_URL=postgres://user:password@localhost:5432/workspace \
  python manage.py loaddata dump.json
```

### 4. Verify

Compare record counts between both databases to make sure nothing was lost.

## Post-migration

1. **Update your configuration** to point `DATABASE_URL` to the PostgreSQL instance
2. **Restart the application** so all workers use the new database
3. **Verify** that login, file uploads, chat, and other features work correctly
4. **Keep the SQLite file** as a backup until you are confident the migration succeeded

## Rollback

The migration does not modify the SQLite file. To roll back:

1. Remove or unset `DATABASE_URL` (Workspace falls back to SQLite)
2. Restart the application
