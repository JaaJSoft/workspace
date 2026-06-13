"""React to file lifecycle events by syncing a file's outgoing links.

Registered with the file-event dispatcher; runs off-request via the
files.run_file_event_handlers task whenever a file is created or has its
content replaced. Markdown is the only content type with parseable note links
today (see services/links.py). The backfill_file_links command covers
pre-existing files.
"""

from __future__ import annotations

from workspace.files.models import FileEvent
from workspace.files.services.event_dispatch import on_file_event
from workspace.files.services.links import reconcile_file_links


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def update_file_links_for_event(event):
    """Re-extract and reconcile a file's outgoing links on create/update."""
    file = event.file
    if file.deleted_at is not None:
        # Trashed before we ran; nothing to index (graph hides deleted nodes).
        return
    reconcile_file_links(file)
