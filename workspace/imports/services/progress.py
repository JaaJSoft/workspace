"""Live progress for the imports page, through the shared per-user SSE mailbox."""

from workspace.core.sse_registry import push_user_event

SLUG = "imports"


def job_payload(job, *, current=""):
    return {
        "type": "imports.job",
        "job": str(job.pk),
        "status": job.status,
        "stats": job.stats,
        "current": current,
        "error": job.error,
    }


def push_job_progress(job, *, current=""):
    """Queue the job's latest state for its owner; an older queued payload for
    the same job is superseded (the UI only needs the newest)."""
    push_user_event(
        SLUG,
        job.connection.owner_id,
        job_payload(job, current=current),
        supersedes=("job", str(job.pk)),
    )
