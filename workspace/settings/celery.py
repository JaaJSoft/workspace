"""Celery broker, task defaults and the beat schedule."""

import os

import kombu
from celery.schedules import crontab

from .base import DEBUG, TIME_ZONE
from .cache import _REDIS_CELERY_URL

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

# How stale an account's last successful sync must be before the dispatcher
# claims it again. Doubles as the beat cadence: the dispatcher runs on this
# period and only picks up accounts whose last_sync_at is older than it, so
# raising it lengthens the polling delay for new mail on instances where the
# IMAP round trips are the bottleneck.
MAIL_SYNC_INTERVAL = float(os.getenv("MAIL_SYNC_INTERVAL", "300"))

CELERY_BEAT_SCHEDULE = {
    "sync-all-user-files": {
        "task": "files.sync_all_users",
        "schedule": FILES_SYNC_INTERVAL,
        # Drop a tick that has not started by the time the next one fires,
        # so a backed-up broker cannot accumulate stale fan-outs.
        "options": {"expires": FILES_SYNC_INTERVAL},
    },
    "generate-thumbnails": {
        "task": "files.generate_thumbnails",
        "schedule": 3600.0,  # Hourly backfill; primary path is event-driven
    },
    "purge-trash": {
        "task": "files.purge_trash",
        "schedule": crontab(hour=2, minute=30),  # Every day at 2:30 AM
    },
    "db-maintenance": {
        "task": "core.db_maintenance",
        # Mon-Sat at 3:00 AM: cheap pass only (optimize + WAL checkpoint).
        # VACUUM and integrity_check are skipped here - both scale with database
        # size and VACUUM holds an exclusive lock plus needs ~2x the file size in
        # free disk, so running them daily stalls every write on large SQLite files.
        "schedule": crontab(hour=3, minute=0, day_of_week="1-6"),
        "kwargs": {"skip_vacuum": True, "skip_integrity_check": True},
    },
    "db-maintenance-full": {
        "task": "core.db_maintenance",
        # Sunday at 3:00 AM: full pass including VACUUM + integrity_check.
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
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
    # Both morning crons merge into any still-unread notification
    # (notify_stream keys on the task/event row), so replaying them is safe.
    "notify-due-tasks": {
        "task": "projects.notify_due_tasks",
        "schedule": crontab(hour=7, minute=0),  # Every day at 7:00 AM
    },
    "notify-today-events": {
        "task": "calendar.notify_today_events",
        "schedule": crontab(hour=7, minute=15),  # Every day at 7:15 AM
    },
}
