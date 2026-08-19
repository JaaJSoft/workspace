"""Importer contract and the context a running job hands to it.

An importer moves one data *kind* from a provider into the module that owns
that kind. It is resumable by construction: a job runs in time slices, and
every slice calls ``run()`` again - the importer continues from the state it
persisted in ``ctx.stats`` and stops as soon as ``ctx.should_stop()`` says so.
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum

from django.utils import timezone

from ..errors import ImportsError
from ..models import ImportJob, ImportJobItem
from ..services import progress

logger = logging.getLogger(__name__)

_STATS_FLUSH_EVERY = 25
_CANCEL_POLL_SECONDS = 2
_PUSH_MIN_INTERVAL_SECONDS = 1.5


class JobFailed(ImportsError):
    """The importer cannot continue; the job ends as FAILED with this message."""


class Outcome(Enum):
    DONE = "done"
    PAUSED = "paused"  # out of time for this slice, call run() again later
    CANCELLED = "cancelled"
    # Runner-level results, never returned by an importer.
    FAILED = "failed"
    SKIPPED = "skipped"


class ImportContext:
    def __init__(self, job: ImportJob, provider, importer, *, deadline=None):
        self.job = job
        self.connection = job.connection
        self.owner = job.connection.owner
        self.provider = provider
        self.importer = importer
        self.kind = importer.kind
        self.options = (job.options or {}).get(self.kind, {})
        self.stats = job.stats.setdefault(self.kind, {})
        self._deadline = deadline
        self._dirty = 0
        self._last_cancel_poll = None
        self._last_push = None
        self._cancelled = False
        self._done = self._load_done()
        self.current = ""

    # -- progress ------------------------------------------------------

    def stat(self, name, delta=1):
        self.stats[name] = self.stats.get(name, 0) + delta

    def set_phase(self, phase):
        self.stats["phase"] = phase
        self.flush(force=True)

    def flush(self, force=False):
        """Persist stats (with a liveness stamp) and push progress, batched."""
        self._dirty += 1
        if not force and self._dirty < _STATS_FLUSH_EVERY:
            return
        self._dirty = 0
        now = timezone.now()
        ImportJob.objects.filter(pk=self.job.pk).update(
            stats=self.job.stats, heartbeat_at=now
        )
        if (
            force
            or self._last_push is None
            or (now - self._last_push).total_seconds() >= _PUSH_MIN_INTERVAL_SECONDS
        ):
            self._last_push = now
            progress.push_job_progress(self.job, current=self.current)

    # -- items ---------------------------------------------------------

    def _load_done(self):
        """remote_id -> fingerprint of the entries already imported *with the
        same options* on this connection and whose target still exists. That
        is what makes a re-run incremental and a retry skip the done part,
        without a second import into another folder being skipped wholesale."""
        job_ids = [
            j.pk
            for j in ImportJob.objects.filter(connection_id=self.connection.pk)
            .exclude(pk=self.job.pk)
            .only("pk", "options")
            if (j.options or {}).get(self.kind) == self.options
        ]
        job_ids.append(self.job.pk)
        rows = list(
            ImportJobItem.objects.filter(
                job_id__in=job_ids,
                kind=self.kind,
                status=ImportJobItem.Status.DONE,
            )
            .exclude(remote_etag="")
            .order_by("created_at")
            .values_list("remote_id", "remote_etag", "target_uuid")
        )
        targets = {t for _, _, t in rows if t is not None}
        alive = self.importer.live_targets(self.owner, targets) if targets else set()
        return {rid: fp for rid, fp, target in rows if target in alive}

    def already_done(self, remote_id, fingerprint="") -> bool:
        return bool(fingerprint) and self._done.get(remote_id) == fingerprint

    def report_item(
        self, remote_id, status, *, target_uuid=None, error="", fingerprint=""
    ):
        ImportJobItem.objects.update_or_create(
            job=self.job,
            kind=self.kind,
            remote_id=remote_id,
            defaults={
                "status": status,
                "target_uuid": target_uuid,
                "error": error[:2000],
                "remote_etag": fingerprint,
            },
        )
        if status == ImportJobItem.Status.DONE and fingerprint:
            self._done[remote_id] = fingerprint
        self.flush()

    # -- control -------------------------------------------------------

    def cancelled(self) -> bool:
        if self._cancelled:
            return True
        now = timezone.now()
        if (
            self._last_cancel_poll is None
            or (now - self._last_cancel_poll).total_seconds() >= _CANCEL_POLL_SECONDS
        ):
            self._last_cancel_poll = now
            self._cancelled = (
                ImportJob.objects.filter(pk=self.job.pk)
                .exclude(cancel_requested_at__isnull=True)
                .exists()
            )
        return self._cancelled

    def out_of_time(self) -> bool:
        return self._deadline is not None and timezone.now() >= self._deadline

    def should_stop(self):
        """``Outcome`` to return right now, or None to keep going."""
        if self.cancelled():
            return Outcome.CANCELLED
        if self.out_of_time():
            return Outcome.PAUSED
        return None


class Importer(ABC):
    kind: str
    #: Serializer validating ``options[kind]``; instantiated with
    #: ``context={"owner": user}``.
    option_serializer: type

    @abstractmethod
    def run(self, ctx: ImportContext) -> Outcome:
        """Import everything not yet done, honouring ``ctx.should_stop()``.
        Raises ``JobFailed`` when the job cannot continue."""

    def live_targets(self, owner, target_uuids) -> set:
        """Subset of *target_uuids* that still exist for *owner*; entries whose
        target is gone are imported again."""
        return set(target_uuids)

    def summarize(self, stats) -> str:
        """One line for the end-of-job notification."""
        return ""


class ImporterRegistry:
    def __init__(self):
        self._importers: dict[str, Importer] = {}

    def register(self, importer: Importer):
        if importer.kind in self._importers:
            raise ValueError(f"Importer for '{importer.kind}' is already registered")
        self._importers[importer.kind] = importer

    def get(self, kind: str) -> Importer | None:
        return self._importers.get(kind)

    def kinds(self) -> list[str]:
        """Registration order is run order."""
        return list(self._importers)


importer_registry = ImporterRegistry()


def register_builtin_importers():
    from .files import FilesImporter

    if importer_registry.get(FilesImporter.kind) is None:
        importer_registry.register(FilesImporter())
