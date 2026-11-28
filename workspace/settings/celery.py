"""Celery broker, task defaults and the beat schedule."""

import os

import kombu
from celery.schedules import crontab

from .base import DEBUG, TIME_ZONE
from .cache import _REDIS_CELERY_URL
from .env import available_cpus, env_non_negative_int
from .storage import STORAGE_BACKEND

# Use dedicated Redis DB as broker if available, otherwise fall back to in-memory
CELERY_BROKER_URL = _REDIS_CELERY_URL or "memory://"
CELERY_TASK_QUEUES = [kombu.Queue("celery")]
CELERY_RESULT_BACKEND = _REDIS_CELERY_URL or "cache+memory://"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes
# Each worker process reserves at most one task beyond the one it runs, instead
# of four: a task that turns up while a background backlog is draining (sent at
# BACKGROUND_PRIORITY) then waits behind one reserved task at most. Acks stay
# early - acks_late would re-run a task whose worker died, which every task
# would then have to tolerate.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Unset means one child process per CPU this container may use. Celery's own
# default counts every core of the host, CPU limit or not: a worker limited to
# 2 CPUs on a 20-core node would fork 20 children. Each child holds its own copy
# of every model its tasks load (file type detection, face analysis), so memory
# grows with this number, not with the load.
CELERY_WORKER_CONCURRENCY = (
    env_non_negative_int("CELERY_WORKER_CONCURRENCY") or available_cpus()
)
# KiB. A child whose resident memory passed this is replaced once its current
# task ends: a single huge image or archive otherwise stays paid for until the
# worker restarts. 0 turns recycling off.
_max_memory_per_child = env_non_negative_int("CELERY_WORKER_MAX_MEMORY_PER_CHILD")
CELERY_WORKER_MAX_MEMORY_PER_CHILD = (
    512 * 1024 if _max_memory_per_child is None else _max_memory_per_child or None
)

# In development, run tasks synchronously in the current thread (no worker needed)
if DEBUG:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# Disk <-> DB reconciliation cadence. Every in-app write (upload, rename,
# move, WebDAV, notes) persists its row synchronously, and the UI can sync
# a folder on demand, so this walk only catches out-of-band disk changes
# (rsync, restore, direct SSH copy). Large deployments can widen it further
# - the trade-off is how long such a change stays invisible in the UI.
FILES_SYNC_INTERVAL = float(os.getenv("FILES_SYNC_INTERVAL", "1800"))

# Cadence of the files catch-up (files/services/processors.py): how long a file
# whose event-driven processing was lost waits before the catch-up takes it,
# and how often a backlog drains another CATCH_UP_LIMIT files per processor.
FILES_CATCH_UP_INTERVAL = float(os.getenv("FILES_CATCH_UP_INTERVAL", "3600"))

# How stale an account's last successful sync must be before the dispatcher
# claims it again. Doubles as the beat cadence: the dispatcher runs on this
# period and only picks up accounts whose last_sync_at is older than it, so
# raising it lengthens the polling delay for new mail on instances where the
# IMAP round trips are the bottleneck.
MAIL_SYNC_INTERVAL = float(os.getenv("MAIL_SYNC_INTERVAL", "300"))

# Where beat records when each periodic task last ran, read by `celery beat`
# and by a worker that runs it embedded (CELERY_WORKER_BEAT=1). A relative path
# resolves against the working directory, as Celery's own default does.
CELERY_BEAT_SCHEDULE_FILENAME = (
    os.getenv("CELERY_BEAT_SCHEDULE_FILENAME") or "celerybeat-schedule"
)

CELERY_BEAT_SCHEDULE = {
    "catch-up-readers": {
        "task": "files.catch_up",
        # The primary path is event-driven; this catches what it missed.
        "schedule": FILES_CATCH_UP_INTERVAL,
        "options": {"expires": FILES_CATCH_UP_INTERVAL},
    },
    # Groups the faces still ungrouped, and deletes any face data a lost
    # purge or move left behind.
    "cluster-faces": {
        "task": "photos.cluster_all_faces",
        "schedule": crontab(hour=2, minute=45),  # Every day at 2:45 AM
    },
    "purge-trash": {
        "task": "files.purge_trash",
        "schedule": crontab(hour=2, minute=30),  # Every day at 2:30 AM
    },
    "purge-file-events": {
        "task": "files.purge_events",
        "schedule": crontab(hour=2, minute=35),  # Every day at 2:35 AM
    },
    # Bytes a killed worker staged and never moved into place: a new file's, or
    # an upload's.
    "purge-staged-blobs": {
        "task": "files.purge_staged_blobs",
        "schedule": crontab(hour=2, minute=40),  # Every day at 2:40 AM
    },
    # Batch operations a killed worker left open, and reports past retention.
    "sweep-file-operations": {
        "task": "files.sweep_file_operations",
        "schedule": crontab(minute="*/10"),
    },
    "purge-stale-uploads": {
        "task": "core.purge_stale_uploads",
        "schedule": crontab(hour=2, minute=40),  # Every day at 2:40 AM
    },
    # Moves a killed worker or a rolled-back transaction left half done.
    "settle-relocations": {
        "task": "files.settle_relocations",
        "schedule": crontab(hour=2, minute=50),  # Every day at 2:50 AM
    },
    # Storage counters a write path missed, reset to the files' total.
    "reconcile-storage-usage": {
        "task": "files.reconcile_storage_usage",
        "schedule": crontab(hour=2, minute=55),  # Every day at 2:55 AM
    },
    # Every day at 3:00 AM: optimize, incremental vacuum, WAL checkpoint. The
    # vacuum frees pages in short write transactions with pauses between them,
    # and the checkpoint is PASSIVE, which never waits for a reader. A full VACUUM is never scheduled: it holds the
    # write lock for the whole rewrite and needs twice the database in free
    # disk; the one a database needs to switch modes runs after migrate.
    "db-maintenance": {
        "task": "core.db_maintenance",
        "schedule": crontab(hour=3, minute=0, day_of_week="1-6"),
    },
    # Sunday at 3:00 AM: the same pass plus an integrity check, which scales
    # with the database's size but only reads.
    "db-maintenance-weekly": {
        "task": "core.db_maintenance",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
        "kwargs": {"integrity_check": True},
    },
    "sync-all-mail-accounts": {
        "task": "mail.sync_all_accounts",
        "schedule": MAIL_SYNC_INTERVAL,
        # The dispatcher is cheap and idempotent (accounts are CAS-claimed),
        # but a tick that has not started by the next one has nothing left to
        # contribute - drop it rather than let stale fan-outs accumulate.
        "options": {"expires": MAIL_SYNC_INTERVAL},
    },
    "dispatch-scheduled-messages": {
        "task": "ai.dispatch_scheduled_messages",
        "schedule": 60.0,  # Every minute
    },
    "dispatch-agent-goals": {
        "task": "ai.dispatch_agent_goals",
        "schedule": 60.0,  # Every minute
    },
    "purge-ai-tasks": {
        "task": "ai.purge_ai_tasks",
        "schedule": crontab(hour=3, minute=30),  # Every day at 3:30 AM
    },
    "purge-import-jobs": {
        "task": "imports.purge_old_jobs",
        "schedule": crontab(hour=3, minute=45),  # Every day at 3:45 AM
    },
    "purge-import-login-flows": {
        "task": "imports.purge_login_flows",
        # Often: it is what ends an abandoned flow's poll token.
        "schedule": 300.0,  # Every 5 minutes
        "options": {"expires": 300.0},
    },
    "recover-stale-import-jobs": {
        "task": "imports.recover_stale_jobs",
        "schedule": 600.0,  # Every 10 minutes
        "options": {"expires": 600.0},
    },
    "purge-orphan-attachments": {
        "task": "chat.purge_orphan_attachments",
        "schedule": crontab(hour=4, minute=0),  # Every day at 4:00 AM
    },
    "prune-read-notifications": {
        "task": "notifications.prune_read",
        "schedule": crontab(hour=4, minute=30),  # Every day at 4:30 AM
    },
    "end-stale-calls": {
        "task": "chat.end_stale_calls",
        "schedule": 60.0,  # Every minute: reap calls whose tabs all vanished
    },
    "sync-external-calendars": {
        "task": "calendar.sync_all_external_calendars",
        "schedule": 900.0,  # Every 15 minutes
    },
    # Hourly, not daily: "due today" is judged against each assignee's local
    # date, which rolls over at a different UTC hour per timezone.
    # TaskReminder rows keep reruns from re-notifying.
    "notify-due-tasks": {
        "task": "projects.notify_due_tasks",
        "schedule": crontab(minute=15),  # Every hour at :15
    },
    # Hourly, not daily: each run notifies the users whose local morning it
    # is. Merges into any still-unread notification (notify_stream keys on
    # the event row), so replaying it is safe.
    "notify-today-events": {
        "task": "calendar.notify_today_events",
        "schedule": crontab(minute=15),  # Every hour at :15
    },
}

# The periodic sync adopts files dropped into MEDIA_ROOT out of band. On object
# storage it would list every user's prefix on a timer to catch a change that
# rarely happens there; the on-demand sync (files UI, sync_files) still runs.
if STORAGE_BACKEND == "local":
    CELERY_BEAT_SCHEDULE["sync-all-user-files"] = {
        "task": "files.sync_all_users",
        "schedule": FILES_SYNC_INTERVAL,
        # Drop a tick that has not started by the time the next one fires,
        # so a backed-up broker cannot accumulate stale fan-outs.
        "options": {"expires": FILES_SYNC_INTERVAL},
    }
