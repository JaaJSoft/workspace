import logging

from celery import shared_task
from django.conf import settings

from .importers.base import Outcome
from .services import jobs

logger = logging.getLogger(__name__)

# A slice yields after IMPORTS_BATCH_SECONDS; the limits leave room for the
# entry in flight to finish, and past them the runner turns the soft limit
# into a pause rather than a failure.
_SOFT_LIMIT = settings.IMPORTS_BATCH_SECONDS + 5 * 60
_HARD_LIMIT = settings.IMPORTS_BATCH_SECONDS + 10 * 60


@shared_task(
    name="imports.run_job",
    bind=True,
    max_retries=0,
    soft_time_limit=_SOFT_LIMIT,
    time_limit=_HARD_LIMIT,
)
def run_import_job(self, job_uuid):
    """Run the job slice by slice. Each slice is a separate task delivery so a
    multi-hour import never meets the Celery time limits.

    In eager mode (no broker, i.e. DEBUG) the slices loop right here, which
    means the request that created the job blocks until the import ends -
    acceptable for development, and the reason the web UI never assumes the
    job is still pending when the create call returns.
    """
    while True:
        result = jobs.run_job(job_uuid)
        if result is not Outcome.PAUSED:
            return {"status": result.value}
        if self.request.is_eager:
            continue
        self.apply_async(args=[job_uuid], countdown=0)
        return {"status": result.value}


@shared_task(name="imports.recover_stale_jobs", ignore_result=True)
def recover_stale_jobs():
    recovered = jobs.recover_stale_jobs()
    if recovered:
        logger.info("Re-enqueued %d stale import jobs", recovered)
    return {"recovered": recovered}


@shared_task(name="imports.purge_old_jobs", ignore_result=True)
def purge_old_jobs():
    deleted = jobs.purge_old_jobs()
    logger.info("Purged %d import job items past retention", deleted)
    return {"deleted": deleted}
