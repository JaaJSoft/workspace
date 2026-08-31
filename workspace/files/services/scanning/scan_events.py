"""React to file lifecycle events by queueing a malware scan.

Registered with the file-event dispatcher, so every write path is covered by
one subscription: REST upload, WebDAV end_write, the office editor's save,
archive extraction, imports and the "save to files" actions all funnel through
FileService and record a CREATED or CONTENT_REPLACED event.

The handler only enqueues. run_handlers() runs every handler for an event
sequentially inside one task, so scanning inline would stall the thumbnail and
link handlers behind a socket transfer of the whole file.
"""

from __future__ import annotations

from django.conf import settings

from ...models import File, FileEvent
from ..event_dispatch import on_file_event


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def scan_file_for_event(event):
    """Queue a malware scan for the event's file."""
    if not getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False):
        return
    file = event.file
    if file.node_type != File.NodeType.FILE or file.deleted_at is not None:
        return

    from workspace.files.tasks import scan_file

    scan_file.delay(str(event.file_id))
