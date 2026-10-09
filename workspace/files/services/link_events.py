"""Register the extraction of a note's outgoing links as an upload processor.

Runs in the upload pipeline whenever a note is created or has its content
replaced, and from the hourly catch-up for whatever that path missed. Markdown
is the only content type with parseable note links today (see
services/links.py).
"""

from __future__ import annotations

from workspace.files.models import File
from workspace.files.services.links import pending_links_qs, refresh_file_links
from workspace.files.services.processors import register_processor
from workspace.files.services.scanning.policy import is_blocked


def is_link_candidate(file_obj):
    return (
        file_obj.node_type == File.NodeType.FILE
        and file_obj.type == "markdown"
        and not is_blocked(file_obj)
    )


register_processor(
    "file_links",
    applies_to=is_link_candidate,
    pending=pending_links_qs,
    process=refresh_file_links,
)
