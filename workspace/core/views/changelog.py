from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from workspace.core import changelog
from workspace.core.setting_keys import CHANGELOG_LAST_SEEN_VERSION, MODULE
from workspace.users.services.settings import get_setting, set_setting


def _entry_was_seen(entry_version, last_seen):
    """Return True if the user has already seen the given changelog version.

    A real numeric version is considered seen when it is less than or equal to
    the user's last-seen version. Non-numeric stored values (e.g. ``'dev'``)
    fall back to a plain equality check so the latest entry is still flagged
    correctly while older ones are left unread.
    """
    if not last_seen:
        return False
    last_seen_tuple = changelog.parse_version(last_seen)
    entry_tuple = changelog.parse_version(entry_version)
    if last_seen_tuple and entry_tuple:
        return entry_tuple <= last_seen_tuple
    return entry_version == last_seen


@login_required
def changelog_partial(request):
    """Return rendered changelog HTML partial for the modal.

    Side effect: stores the topmost changelog version as the user's last-seen
    version, so subsequent page loads stop auto-opening the modal until a new
    entry is added to CHANGELOG.md.
    """
    raw_entries = changelog.get_changelog_entries()
    last_seen = get_setting(
        request.user,
        MODULE,
        CHANGELOG_LAST_SEEN_VERSION,
    )
    entries = [
        {**entry, "read": _entry_was_seen(entry["version"], last_seen)}
        for entry in raw_entries
    ]

    latest = raw_entries[0]["version"] if raw_entries else None
    if latest and last_seen != latest:
        set_setting(
            request.user,
            MODULE,
            CHANGELOG_LAST_SEEN_VERSION,
            latest,
        )

    return render(
        request,
        "core/partials/changelog.html",
        {
            "entries": entries,
        },
    )
