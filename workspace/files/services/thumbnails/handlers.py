"""Register thumbnail generation as an upload processor.

Runs in the upload pipeline once an image or a video has been created or had
its content replaced, after the malware scan, and from the hourly catch-up for
whatever that path missed (services/processors.py).
"""

from __future__ import annotations

from workspace.files.services.processors import register_processor
from workspace.files.services.thumbnails.generation import (
    is_thumbnail_candidate,
    pending_thumbnails_qs,
    refresh_thumbnail,
)

register_processor(
    "thumbnails",
    applies_to=is_thumbnail_candidate,
    pending=pending_thumbnails_qs,
    process=refresh_thumbnail,
    # Right behind the scan: the preview is what a user waits for.
    order=10,
)
