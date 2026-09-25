"""Registry of the per-file readers the hourly catch-up keeps up to date.

A reader computes data derived from a file's content (its MediaInfo row, its
photo-library row...) and runs first from its own file-event handler, when an
upload or a content replacement commits. The catch-up is the safety net for
everything that path missed: files older than the reader, a lost dispatch, a
blob that could not be read, a reader whose output format changed.

Each reader registers which files are pending and how to process one, plus,
optionally, whether it runs at all on this deployment. The catch-up owns
everything else: paging, queueing one task per file, the bound, the priority
and the expiry (see files/tasks.py).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from django.core.cache import cache

from workspace.common.task_priority import BACKGROUND_PRIORITY
from workspace.common.uuids import parse_uuid_or_none

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from ..models import File

# Rows fetched per keyset page by pending_ids. A read cursor held open across
# the write transactions of a loop over it makes SQLite raise "database is
# locked" (tasks run inline in development), so the selection is paged rather
# than streamed.
_PAGE_SIZE = 200

# The last file a pass queued, per reader: the next pass starts right after
# it. Starting from the smallest uuid every time would hand each pass the same
# oldest files, and a reader with more files that keep failing than a pass
# takes would never reach the ones behind them.
_CURSOR_KEY = "files:catch_up:{}:cursor"


class PendingQuery(Protocol):
    def __call__(self, *, reanalyze: bool = False) -> QuerySet[File]:
        """Without *reanalyze*, the files whose derived data is missing or
        stale; with it, every file the reader reads, up to date or not."""


class Process(Protocol):
    def __call__(self, file_obj: File, /) -> bool:
        """Compute and store *file_obj*'s derived data.

        False when nothing was stored: the file stays pending for a later pass.
        """


def _always():
    return True


@dataclass(frozen=True)
class CatchUp:
    name: str
    pending: PendingQuery
    process: Process
    # False on a deployment where the reader cannot run (no ffprobe, scanning
    # switched off): nothing is pending then, so nothing is queued for nothing.
    enabled: Callable[[], bool] = field(default=_always)

    def pending_files(self, *, reanalyze=False):
        """The reader's pending files, or none while it is disabled."""
        if not self.enabled():
            from ..models import File

            return File.objects.none()
        return self.pending(reanalyze=reanalyze)


_CATCH_UPS: dict[str, CatchUp] = {}


def register_catch_up(name, *, pending, process, enabled=_always):
    """Declare a reader to the hourly catch-up; return the registered entry."""
    catch_up = CatchUp(name=name, pending=pending, process=process, enabled=enabled)
    _CATCH_UPS[name] = catch_up
    return catch_up


def registered_catch_ups():
    return list(_CATCH_UPS.values())


def get_catch_up(name):
    return _CATCH_UPS.get(name)


def resolve(names):
    """The readers *names* designate - all of them when empty - and the unknown names."""
    if not names:
        return registered_catch_ups(), []
    names = list(dict.fromkeys(names))
    unknown = [name for name in names if name not in _CATCH_UPS]
    return [_CATCH_UPS[name] for name in names if name in _CATCH_UPS], unknown


def pending_ids(catch_up, *, reanalyze=False, limit=None, start_after=None):
    """Yield the uuids of *catch_up*'s pending files in uuid order.

    From just after *start_after* when given, wrapping around to the start
    once the end is reached, so every pending file comes up in turn.
    """
    queryset = catch_up.pending_files(reanalyze=reanalyze)
    ids = _keyset(queryset, after=start_after)
    if start_after is not None:
        ids = itertools.chain(ids, _keyset(queryset, until=start_after))
    yield from itertools.islice(ids, limit)


def _keyset(queryset, *, after=None, until=None):
    last = after
    while True:
        page_qs = queryset.order_by("uuid")
        if last is not None:
            page_qs = page_qs.filter(uuid__gt=last)
        if until is not None:
            page_qs = page_qs.filter(uuid__lte=until)
        page = list(page_qs.values_list("uuid", flat=True)[:_PAGE_SIZE])
        if not page:
            return
        yield from page
        last = page[-1]


def queue_pending(catch_up, *, reanalyze=False, limit=None, expires=None, resume=False):
    """Queue one files.catch_up_file task per pending file; return how many.

    With *resume*, start after the last file the previous resumed call queued
    for this reader (see _CURSOR_KEY).
    """
    from ..tasks import catch_up_file

    cursor_key = _CURSOR_KEY.format(catch_up.name)
    start_after = parse_uuid_or_none(cache.get(cursor_key)) if resume else None
    queued = 0
    last_uuid = None
    for uuid in pending_ids(
        catch_up, reanalyze=reanalyze, limit=limit, start_after=start_after
    ):
        catch_up_file.apply_async(
            args=[catch_up.name, str(uuid)],
            kwargs={"reanalyze": reanalyze},
            priority=BACKGROUND_PRIORITY,
            expires=expires,
        )
        queued += 1
        last_uuid = uuid
    if resume:
        if last_uuid is None:
            cache.delete(cursor_key)
        else:
            cache.set(cursor_key, str(last_uuid), timeout=None)
    return queued
