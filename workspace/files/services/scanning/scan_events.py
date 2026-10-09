"""Register the malware scan as the first upload processor.

Every write path is covered by the one registration: REST upload, WebDAV
end_write, the office editor's save, archive extraction, imports and the "save
to files" actions all funnel through FileService and record a CREATED or
CONTENT_REPLACED event, which queues the upload pipeline. The scan runs first
there, so the processors after it see the verdict before reading the bytes.
The hourly catch-up scans whatever that path missed.
"""

from __future__ import annotations

from ...models import File
from ..processors import SCAN_ORDER, register_processor
from .scan import pending_scan_qs, scan_for_catch_up, scanning_enabled


def is_scan_candidate(file_obj):
    return file_obj.node_type == File.NodeType.FILE and bool(file_obj.content)


register_processor(
    "malware_scan",
    applies_to=is_scan_candidate,
    pending=pending_scan_qs,
    process=scan_for_catch_up,
    enabled=scanning_enabled,
    order=SCAN_ORDER,
)
