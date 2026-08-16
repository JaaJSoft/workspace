"""Datetime presentation helpers shared across modules."""

from django.utils import timezone


def time_ago(value, now=None):
    """Format an aware datetime as a relative-time label.

    Single source of truth for "time ago" strings on the server, mirrored by
    ``formatTimeAgo`` in ``common/static/ui/js/timeago.js`` - keep the two in
    sync so server- and client-rendered timestamps on the same page match.

    Buckets: ``just now`` (under a minute), ``5m ago``, ``2h ago``, ``3d ago``
    (under a week), then the absolute date - ``Feb 01`` within the current
    year, ``Feb 01, 2025`` otherwise - evaluated in the active timezone.
    """
    if not value:
        return ""
    if now is None:
        now = timezone.now()
    diff = (now - value).total_seconds()
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    if diff < 604800:
        return f"{int(diff // 86400)}d ago"
    local = timezone.localtime(value)
    if local.year == timezone.localtime(now).year:
        return local.strftime("%b %d")
    return local.strftime("%b %d, %Y")
