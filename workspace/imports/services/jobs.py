"""Job lifecycle: create (validated, enqueued), run in time slices, cancel,
retry, purge."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from workspace.common.logging import scrub
from workspace.common.task_locks import task_lock
from workspace.notifications.services.notifications import notify

from ..errors import ImportsError
from ..importers.base import ImportContext, JobFailed, Outcome, importer_registry
from ..models import ImportJob
from . import progress
from .connections import get_available_provider

logger = logging.getLogger(__name__)


class InvalidJob(ImportsError):
    pass


class InvalidJobOptions(ImportsError):
    def __init__(self, errors):
        super().__init__("Invalid import options.")
        self.errors = errors


class JobAlreadyRunning(ImportsError):
    pass


# -- creation ------------------------------------------------------------


def create_job(owner, connection, kinds, options=None):
    """Validate, persist and enqueue a job. ``options`` is ``{kind: {...}}``;
    each kind's importer validates its own part."""
    provider = get_available_provider(connection.provider)
    kinds = list(dict.fromkeys(kinds or []))
    if not kinds:
        raise InvalidJob("Pick at least one thing to import.")
    ordered = [k for k in importer_registry.kinds() if k in kinds]
    unknown = set(kinds) - set(ordered)
    if unknown:
        raise InvalidJob(f"Nothing knows how to import {', '.join(sorted(unknown))}.")
    unsupported = set(kinds) - provider.kinds
    if unsupported:
        raise InvalidJob(
            f"{provider.name} cannot provide {', '.join(sorted(unsupported))}."
        )

    validated, errors = {}, {}
    for kind in ordered:
        importer = importer_registry.get(kind)
        ser = importer.option_serializer(
            data=(options or {}).get(kind, {}), context={"owner": owner}
        )
        try:
            ser.is_valid(raise_exception=True)
        except serializers.ValidationError as exc:
            errors[kind] = exc.detail
            continue
        validated[kind] = ser.validated_data
    if errors:
        raise InvalidJobOptions(errors)

    if connection.jobs.exclude(status__in=ImportJob.TERMINAL_STATUSES).exists():
        raise JobAlreadyRunning("An import is already running for this connection.")

    job = ImportJob.objects.create(
        connection=connection, kinds=ordered, options=validated
    )
    transaction.on_commit(lambda: _enqueue(job))
    return job


def _enqueue(job):
    from ..tasks import run_import_job

    run_import_job.delay(str(job.pk))


def cancel_job(job):
    if job.is_terminal:
        raise InvalidJob("This import is already finished.")
    now = timezone.now()
    # A pending job has no worker to notice the flag: end it right here. The
    # CAS on status keeps a worker that starts at the same instant honest.
    ended = ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.PENDING).update(
        status=ImportJob.Status.CANCELLED, cancel_requested_at=now, finished_at=now
    )
    if not ended:
        ImportJob.objects.filter(pk=job.pk).update(cancel_requested_at=now)
    job.refresh_from_db()
    progress.push_job_progress(job, force=True)
    return job


def retry_job(job):
    """A fresh job with the same settings; entries already done are skipped
    by the importer, so only the failed and unprocessed ones run again."""
    if job.status not in (ImportJob.Status.FAILED, ImportJob.Status.CANCELLED):
        raise InvalidJob("Only a failed or cancelled import can be retried.")
    return create_job(job.connection.owner, job.connection, job.kinds, job.options)


def purge_old_jobs():
    cutoff = timezone.now() - timedelta(days=settings.IMPORTS_JOB_RETENTION_DAYS)
    deleted, _ = ImportJob.objects.filter(
        status__in=ImportJob.TERMINAL_STATUSES, finished_at__lt=cutoff
    ).delete()
    return deleted


# -- execution -----------------------------------------------------------


def run_job(job_uuid):
    """Run one time slice of the job. Returns ``"done"``, ``"failed"``,
    ``"cancelled"``, ``"paused"`` (call again) or ``"skipped"`` (nothing to do:
    already finished, cancelled before it started, or running elsewhere)."""
    job = (
        ImportJob.objects.select_related("connection__owner")
        .filter(pk=job_uuid)
        .first()
    )
    if job is None:
        return "skipped"
    if job.status == ImportJob.Status.PENDING:
        claimed = ImportJob.objects.filter(
            pk=job.pk, status=ImportJob.Status.PENDING
        ).update(status=ImportJob.Status.RUNNING, started_at=timezone.now())
        if not claimed:
            return "skipped"
        job.refresh_from_db()
        progress.push_job_progress(job, force=True)
    elif job.status != ImportJob.Status.RUNNING:
        return "skipped"

    with task_lock(f"imports:job:{job.pk}", settings.IMPORTS_BATCH_SECONDS * 2) as held:
        if not held:
            return "skipped"
        deadline = timezone.now() + timedelta(seconds=settings.IMPORTS_BATCH_SECONDS)
        try:
            outcome = _run_slice(job, deadline)
        except ImportsError as exc:
            _finish(job, ImportJob.Status.FAILED, error=exc.user_message)
            return "failed"
        except Exception:
            logger.exception("Import job %s crashed", job.pk)
            _finish(
                job,
                ImportJob.Status.FAILED,
                error="Unexpected error, see the server logs.",
            )
            return "failed"

    if outcome is Outcome.DONE:
        _finish(job, ImportJob.Status.COMPLETED)
        return "done"
    if outcome is Outcome.CANCELLED:
        _finish(job, ImportJob.Status.CANCELLED)
        return "cancelled"
    ImportJob.objects.filter(pk=job.pk).update(stats=job.stats)
    return "paused"


def _run_slice(job, deadline):
    provider = get_available_provider(job.connection.provider)
    for kind in job.kinds:
        if job.stats.get(kind, {}).get("phase") == "done":
            continue
        importer = importer_registry.get(kind)
        if importer is None:
            raise JobFailed(f"Nothing knows how to import {kind}.")
        ctx = ImportContext(job, provider, kind, deadline=deadline)
        outcome = importer.run(ctx)
        if outcome is not Outcome.DONE:
            return outcome
    return Outcome.DONE


def _finish(job, status, *, error=""):
    ImportJob.objects.filter(pk=job.pk).update(
        status=status, error=error, finished_at=timezone.now(), stats=job.stats
    )
    job.refresh_from_db()
    progress.push_job_progress(job, force=True)
    _notify_owner(job)


def _notify_owner(job):
    label = job.connection.label
    if job.status == ImportJob.Status.COMPLETED:
        title = f"Import from {label} finished"
        body = _summary(job)
    elif job.status == ImportJob.Status.FAILED:
        title = f"Import from {label} failed"
        body = job.error
    else:
        title = f"Import from {label} cancelled"
        body = _summary(job)
    try:
        notify(
            recipient=job.connection.owner,
            origin="imports",
            title=title,
            body=body,
            url=f"/imports?job={job.pk}",
        )
    except Exception:
        logger.exception("Could not notify about import job %s", scrub(str(job.pk)))


def _summary(job):
    parts = []
    files = job.stats.get("files") or {}
    if files.get("files"):
        parts.append(f"{files['files']} files")
    if files.get("skipped"):
        parts.append(f"{files['skipped']} skipped")
    if files.get("failed"):
        parts.append(f"{files['failed']} failed")
    return ", ".join(parts) or "Nothing to import."
