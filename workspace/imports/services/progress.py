"""Live progress for the imports page: per-user SSE mailbox, same shape as
the files module's event fan-out."""

from django.core.cache import cache
from django.utils import timezone

from workspace.core.sse_registry import notify_sse

PENDING_EVENTS_KEY = "imports:pending_events:{user_id}"
PENDING_EVENTS_TTL = 300
_MIN_INTERVAL_SECONDS = 1.5

_last_push = {}


def job_payload(job, *, current=""):
    return {
        "type": "imports.job",
        "job": str(job.pk),
        "status": job.status,
        "stats": job.stats,
        "current": current,
        "error": job.error,
    }


def push_job_progress(job, *, current="", force=False):
    """Queue a progress event for the job's owner, at most every ~1.5 s unless
    forced (status changes always go through)."""
    now = timezone.now()
    last = _last_push.get(job.pk)
    if not force and last and (now - last).total_seconds() < _MIN_INTERVAL_SECONDS:
        return
    _last_push[job.pk] = now
    user_id = job.connection.owner_id
    key = PENDING_EVENTS_KEY.format(user_id=user_id)
    events = cache.get(key, [])
    # One event per job is enough: the newest payload supersedes the older.
    events = [e for e in events if e.get("job") != str(job.pk)]
    events.append(job_payload(job, current=current))
    cache.set(key, events, PENDING_EVENTS_TTL)
    notify_sse("imports", user_id)
