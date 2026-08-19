"""Importer contract and the context a running job hands to it.

An importer moves one data *kind* from a provider into the module that owns
that kind. It is resumable by construction: a job runs in time slices, and
every slice calls ``run()`` again - the importer skips what ``ctx`` already
records as done and stops as soon as ``ctx.should_stop()`` says so.
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


class JobFailed(ImportsError):
    """The importer cannot continue; the job ends as FAILED with this message."""


class Outcome(Enum):
    DONE = "done"
    PAUSED = "paused"  # out of time for this slice, call run() again later
    CANCELLED = "cancelled"


class ImportContext:
    def __init__(self, job: ImportJob, provider, kind: str, *, deadline=None):
        self.job = job
        self.connection = job.connection
        self.owner = job.connection.owner
        self.provider = provider
        self.kind = kind
        self.options = (job.options or {}).get(kind, {})
        self.stats = job.stats.setdefault(kind, {})
        self._deadline = deadline
        self._dirty = 0
        self._last_cancel_poll = None
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
        self._dirty += 1
        if not force and self._dirty < _STATS_FLUSH_EVERY:
            return
        self._dirty = 0
        ImportJob.objects.filter(pk=self.job.pk).update(stats=self.job.stats)
        progress.push_job_progress(self.job, current=self.current)

    # -- items ---------------------------------------------------------

    def _load_done(self):
        """remote_id -> etag of every entry this connection already imported
        (any job): what makes a re-run incremental and a retry skip the done part."""
        rows = (
            ImportJobItem.objects.filter(
                job__connection_id=self.connection.pk,
                kind=self.kind,
                status=ImportJobItem.Status.DONE,
            )
            .order_by("created_at")
            .values_list("remote_id", "remote_etag")
        )
        return dict(rows)

    def already_done(self, remote_id, etag="") -> bool:
        return remote_id in self._done and self._done[remote_id] == etag

    def report_item(self, remote_id, status, *, target_uuid=None, error="", etag=""):
        ImportJobItem.objects.update_or_create(
            job=self.job,
            kind=self.kind,
            remote_id=remote_id,
            defaults={
                "status": status,
                "target_uuid": target_uuid,
                "error": error[:2000],
                "remote_etag": etag,
            },
        )
        if status == ImportJobItem.Status.DONE:
            self._done[remote_id] = etag
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
