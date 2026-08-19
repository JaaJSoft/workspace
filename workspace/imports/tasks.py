import logging

from celery import shared_task

from .services import jobs

logger = logging.getLogger(__name__)


@shared_task(name="imports.run_job", bind=True, max_retries=0)
def run_import_job(self, job_uuid):
    """Run the job slice by slice. Each slice is a separate task delivery so a
    multi-hour import never meets the Celery time limits; in eager mode (no
    broker) the slices simply loop here."""
    while True:
        result = jobs.run_job(job_uuid)
        if result != "paused":
            return {"status": result}
        if self.request.is_eager:
            continue
        self.apply_async(args=[job_uuid], countdown=0)
        return {"status": "paused"}


@shared_task(name="imports.purge_old_jobs", ignore_result=True)
def purge_old_jobs():
    deleted = jobs.purge_old_jobs()
    logger.info("Purged %d import jobs past retention", deleted)
    return {"deleted": deleted}
