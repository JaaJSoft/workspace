"""Lifting a quarantine by hand, and putting back what quarantining removed.

Re-scanning is the answer to a verdict that has gone stale; it is not an
answer to a verdict that is simply wrong. The same bytes fed to the same
signature database come back infected every time, so a false positive needs a
human decision recorded next to the verdict rather than in place of it.
"""

from __future__ import annotations

from django.utils import timezone

from .policy import blocked_statuses

# What mark_safe did. Three outcomes rather than a boolean, because the two
# refusals need different words in front of an operator: NOT_BLOCKED is a
# no-op on a file nothing was withholding, UNPINNABLE is a file that stays
# blocked and needs something done first.
LIFTED = "lifted"
NOT_BLOCKED = "not_blocked"
UNPINNABLE = "unpinnable"


def restore_after_unblock(file_obj):
    """Give back what quarantining took away from *file_obj*.

    Called from both ways out of quarantine - a fresh verdict and an
    administrator's override - so the two cannot restore different things.

    The search document has to be rebuilt here: nothing else does it, since
    reindexing is a manual command and not a periodic pass. The thumbnail
    would eventually come back on its own through generate_missing_thumbnails,
    but an hour of a blank preview after somebody said the file was fine is a
    poor answer, so it is regenerated now.
    """
    from ..search_index import index_file
    from ..thumbnails.generation import can_generate_thumbnail, generate_thumbnail

    index_file(file_obj)

    if file_obj.has_thumbnail or not can_generate_thumbnail(file_obj.type):
        return
    if generate_thumbnail(file_obj):
        file_obj.has_thumbnail = True
        file_obj.save(update_fields=["has_thumbnail"])


def mark_safe(scan, *, user, reason=""):
    """Record that *user* judged *scan*'s detection to be a false positive.

    Returns one of LIFTED / NOT_BLOCKED / UNPINNABLE, so a caller acting on a
    selection can report what happened to each row. A verdict the policy does
    not block needs no override, and writing one would only leave a puzzling
    annotation on a clean row.

    A verdict that does not describe the file's current bytes is refused: the
    override pins itself to that hash, so recording one here would leave the
    file blocked behind a success message. Re-scan it first - or backfill the
    hashes, if the file never had one.
    """
    if scan.status not in blocked_statuses():
        return NOT_BLOCKED
    if not scan.content_hash or scan.content_hash != scan.file.content_hash:
        return UNPINNABLE

    scan.overridden_at = timezone.now()
    scan.overridden_by = user
    scan.override_reason = reason
    scan.save(update_fields=["overridden_at", "overridden_by", "override_reason"])

    restore_after_unblock(scan.file)
    return LIFTED
