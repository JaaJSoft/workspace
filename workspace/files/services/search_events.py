"""React to file lifecycle events by refreshing the full-text search document.

Registered with the file-event dispatcher. The handler only enqueues: reading
a blob and extracting its text is slow enough that doing it inline would delay
every other handler for the same event (thumbnails, link previews).

Trashing needs no handler - the row and its document both stay, and the access
querysets already hide a trashed file from search. Hard deletion is handled by
the pre_delete receiver in models.py, which still has a resolvable row.
"""

from __future__ import annotations

import logging

from workspace.files.models import FileEvent
from workspace.files.services.event_dispatch import on_file_event

logger = logging.getLogger(__name__)


@on_file_event(
    FileEvent.Action.CREATED,
    FileEvent.Action.CONTENT_REPLACED,
    FileEvent.Action.RENAMED,
    FileEvent.Action.RESTORED,
)
def index_search_document_for_event(event):
    """Queue a re-index of the event's file."""
    from workspace.files.tasks import index_search_document

    # A copy records one CREATED event for the subtree root; every other
    # action leaves descendant names and contents untouched.
    include_descendants = event.action == FileEvent.Action.CREATED
    index_search_document.delay(str(event.file_id), include_descendants)
