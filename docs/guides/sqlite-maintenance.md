# SQLite maintenance

On SQLite, Workspace keeps its database in shape on its own: a task runs every night at 03:00 and hands the space freed by deleted rows back to the disk, a little at a time, so nobody notices it. This page covers what that task does, the one-off step an existing installation needs to benefit from it, and how to take a consistent copy of the database.

None of it applies to PostgreSQL, which has its own autovacuum.

## The nightly pass

Every night the scheduler runs `core.db_maintenance`:

1. `PRAGMA optimize` refreshes the statistics the query planner uses.
2. An **incremental vacuum** returns free pages to the filesystem, 1000 pages (4 MB) per step. Each step is its own short write transaction, and the pass pauses between two steps so that writes waiting for the lock get in. It stops after a minute and leaves the rest for the next night.
3. A WAL checkpoint (`TRUNCATE`) copies the write-ahead log into the database and shrinks the file to what it now holds.

On Sundays the same pass also runs `PRAGMA integrity_check`. It reads the whole database but writes nothing: writes keep going while it runs.

The worker log reports what each pass did:

```text
SQLite maintenance finished in 1830 ms (freed 12000 pages in 12 steps, 0 free pages left, size 812646400 -> 763494400 bytes)
```

No scheduled task rewrites the whole database. A full `VACUUM` holds the write lock from start to finish, so on a large file every write waits for it and fails after 60 seconds with "database is locked". It only runs when you start it yourself, as below.

## Switching an existing database to incremental mode

A database created by Workspace 0.46 or earlier stays in `auto_vacuum = NONE` mode: the nightly pass cannot shrink it, and logs a warning saying so every night. SQLite only changes that mode by rewriting the file once, with a full `VACUUM`. Databases created since are in incremental mode from the start and need nothing.

Run the switch once, at a quiet time: while it runs every write waits, and a write that waits more than 60 seconds fails.

```bash
docker compose exec web python manage.py db_maintenance --full-vacuum
```

```bash
kubectl -n workspace exec deploy/workspace -c web -- python manage.py db_maintenance --full-vacuum
```

It prints the mode before and after:

```text
  VACUUM: 41237.5 ms, auto_vacuum none -> incremental
```

The rewrite needs about twice the database's size in free disk: SQLite builds the new copy in its temporary directory (`SQLITE_TMPDIR`, then `TMPDIR`, then `/var/tmp` or `/tmp`), then writes it again through the write-ahead log next to the database. The command checks both disks before starting and refuses with the space it needs and the space it found:

```text
CommandError: Not enough free disk for a full VACUUM: /app/data: 896 MB needed, 412 MB free
```

Free some disk, or point `SQLITE_TMPDIR` at a larger volume, and run it again. The same command also rewrites a database that is already in incremental mode, should it ever need compacting in one go.

`python manage.py db_maintenance` without options runs the nightly pass by hand, and `--integrity-check` adds the integrity check to either form.

## Snapshots

```bash
docker compose exec web python manage.py db_snapshot /app/data/backups/db-2026-11-26.sqlite3
```

`db_snapshot` writes a compacted copy of the database with `VACUUM INTO`. The copy is consistent, taken at one instant, and is written from a read transaction: the instance keeps working while it runs. The command refuses a path that already exists and checks that the destination disk can hold the copy.

The copy holds the database only. Uploaded files live next to it in `/app/data` (or in the object storage bucket) and need a backup of their own. To restore, stop the instance and put the copy in place of `db.sqlite3`, after removing the `db.sqlite3-wal` and `db.sqlite3-shm` files beside it.
