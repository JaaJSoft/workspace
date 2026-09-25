"""Registry of the per-file readers the hourly catch-up keeps up to date.

A reader computes data derived from a file's content (its MediaInfo row, its
photo-library row...) and runs first from its own file-event handler, when an
upload or a content replacement commits. The catch-up is the safety net for
everything that path missed: files older than the reader, a lost dispatch, a
blob that could not be read, a reader whose output format changed.

Each reader registers two functions: which files are pending, and how to
process one. The catch-up owns everything else: paging, queueing one task
per file, the bound, the priority and the expiry (see files/tasks.py).
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db.models import QuerySet

# Rows fetched per keyset page by pending_ids. A read cursor held open across
# the write transactions of a loop over it makes SQLite raise "database is
# locked" (tasks run inline in development), so the selection is paged rather
# than streamed.
_PAGE_SIZE = 200


@dataclass(frozen=True)
class CatchUp:
    name: str
    # (*, reanalyze=False) -> File queryset. Without reanalyze, the live files
    # whose derived data is missing or stale; with it, every file the reader
    # reads, up to date or not.
    pending: Callable[..., QuerySet]
    # (file) -> truthy when it stored the file's derived data, falsy when it
    # could not (the file stays pending for the next pass).
    process: Callable


_CATCH_UPS: dict[str, CatchUp] = {}


def register_catch_up(name, *, pending, process):
    """Declare a reader to the hourly catch-up; return the registered entry."""
    catch_up = CatchUp(name=name, pending=pending, process=process)
    _CATCH_UPS[name] = catch_up
    return catch_up


def registered_catch_ups():
    return list(_CATCH_UPS.values())


def get_catch_up(name):
    return _CATCH_UPS.get(name)


def pending_ids(catch_up, *, reanalyze=False, limit=None):
    """Yield the uuids of *catch_up*'s pending files, paged by keyset."""
    queryset = catch_up.pending(reanalyze=reanalyze)
    produced = 0
    last_uuid = None
    while True:
        page_qs = queryset.order_by("uuid")
        if last_uuid is not None:
            page_qs = page_qs.filter(uuid__gt=last_uuid)
        page = list(page_qs.values_list("uuid", flat=True)[:_PAGE_SIZE])
        if not page:
            return
        last_uuid = page[-1]
        for uuid in page:
            if limit is not None and produced >= limit:
                return
            yield uuid
            produced += 1
