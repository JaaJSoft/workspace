"""Extract and persist the links between files (note graph edges).

Today the only producer of links is the ``[[`` wikilink autocomplete in the
markdown editor, which inserts ``[Title](/notes?file=UUID)`` into a note's
content. ``extract_link_targets`` parses those references and
``reconcile_file_links`` syncs them into FileLink rows for a source file, and
records what it read in FileLinkState so the hourly catch-up
(services/catch_up.py) can tell which notes still need it.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from workspace.common.uuids import parse_uuid_or_none

from ..models import File, FileLink, FileLinkState
from ._content import read_text_content
from .scanning.policy import exclude_blocked

# Matches the ``?file=<uuid>`` / ``&file=<uuid>`` token the autocomplete emits.
# 36 chars covers the canonical hyphenated UUID form Django serializes.
_LINK_RE = re.compile(r"[?&]file=([0-9a-fA-F-]{36})")

# Raise when extract_link_targets() starts finding links it did not before:
# every note reconciled by an older version becomes pending, and the catch-up
# re-reads them on its own.
EXTRACTOR_VERSION = 1


def extract_link_targets(markdown_text) -> set[str]:
    """Return the distinct, validated note-link target UUIDs as canonical strings.

    Pure: no DB access. Captures are validated with ``parse_uuid_or_none`` so a
    36-character token that is not a real UUID is dropped. A captured UUID that
    does not resolve to an existing File is dropped later, in reconcile.
    """
    if not markdown_text:
        return set()
    targets = set()
    for raw in _LINK_RE.findall(markdown_text):
        parsed = parse_uuid_or_none(raw)
        if parsed is not None:
            targets.add(str(parsed))
    return targets


# Notes are text; cap the scan to bound memory. The shared read_text_content
# helper defaults to 32 KB, which would truncate long notes and miss links.
LINK_SCAN_MAX_BYTES = 1_048_576  # 1 MiB


def reconcile_file_links(file):
    """Sync FileLink rows for ``source=file`` with the links in its content.

    Markdown-only today (the only content type with parseable note links).
    Idempotent. Returns the set of resolved target UUIDs, or ``None`` when the
    file is skipped (folder / non-markdown). Emptying a note's content clears
    its outgoing edges. Best-effort: callers run it off-request.
    """
    if file.node_type != File.NodeType.FILE or file.type != "markdown":
        return None

    text = read_text_content(file, max_bytes=LINK_SCAN_MAX_BYTES)
    if text is None:
        # Could not read the content (IO/decode error, or no stored blob).
        # Leave existing edges untouched: clearing them on a transient read
        # failure would silently drop the note's graph. A genuinely empty note
        # decodes to "" and still reconciles to zero edges below.
        return None
    candidates = extract_link_targets(text)

    # Resolve to existing files (FK integrity + drops false positives), never
    # self. Deleted state is NOT filtered here - that is a display concern for
    # the graph read; the edge stays structurally valid.
    targets = set(
        File.objects.filter(uuid__in=candidates)
        .exclude(uuid=file.uuid)
        .values_list("uuid", flat=True)
    )
    existing = set(
        FileLink.objects.filter(source=file).values_list("target_id", flat=True)
    )

    to_remove = existing - targets
    to_add = targets - existing
    # One transaction so a single reconcile is all-or-nothing: a failed create
    # can never leave the prior edges already deleted, nor the state claiming
    # edges that were never written.
    with transaction.atomic():
        if to_remove:
            FileLink.objects.filter(source=file, target_id__in=to_remove).delete()
        if to_add:
            FileLink.objects.bulk_create(
                [FileLink(source=file, target_id=t) for t in to_add],
                ignore_conflicts=True,  # tolerate a racing concurrent reconcile
            )
        # The hash the text was read under, not a fresh read: an edit landing
        # meanwhile leaves the row behind the file, which is what makes the
        # catch-up reconcile it again.
        FileLinkState.objects.update_or_create(
            file_id=file.pk,
            defaults={
                "content_hash": file.content_hash,
                "extractor_version": EXTRACTOR_VERSION,
                "reconciled_at": timezone.now(),
            },
        )
    return targets


def pending_links_qs(*, reanalyze=False):
    """Live notes whose outgoing links were never extracted, or from other bytes.

    With *reanalyze*, every live note.
    """
    qs = exclude_blocked(File.objects.alive().with_blob().filter(type="markdown"))
    if reanalyze:
        return qs
    return qs.filter(
        Q(link_state__isnull=True)
        | ~Q(link_state__content_hash=F("content_hash"))
        | Q(link_state__extractor_version__lt=EXTRACTOR_VERSION)
    )


def refresh_file_links(file):
    """Reconcile *file*'s links; True when its content could be read."""
    return reconcile_file_links(file) is not None
