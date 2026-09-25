"""React to file lifecycle events by syncing a file's outgoing links.

Registered with the file-event dispatcher; runs off-request via the
files.run_file_event_handlers task whenever a file is created or has its
content replaced. Markdown is the only content type with parseable note links
today (see services/links.py). Registered with the hourly catch-up too, which
reconciles whatever that path missed.
"""

from __future__ import annotations

from workspace.files.models import FileEvent
from workspace.files.services.catch_up import register_catch_up
from workspace.files.services.event_dispatch import on_file_event
from workspace.files.services.links import (
    pending_links_qs,
    reconcile_file_links,
    refresh_file_links,
)


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def update_file_links_for_event(event):
    """Re-extract and reconcile a file's outgoing links on create/update."""
    file = event.file
    if file.deleted_at is not None:
        # Trashed before we ran; nothing to index (graph hides deleted nodes).
        return
    reconcile_file_links(file)


register_catch_up("file_links", pending=pending_links_qs, process=refresh_file_links)
