"""Whether faces are detected, instance-wide and for one user.

Opt-in twice: the instance allows it (PHOTOS_FACES_ENABLED) and the user
turned it on (the `photos` / `faces_enabled` user setting). Turning the
setting off purges everything computed for that user (see services/handlers.py).
"""

from django.conf import settings

from workspace.users.models import UserSetting
from workspace.users.services.settings import get_setting

MODULE = "photos"
FACES_ENABLED = "faces_enabled"


def faces_available():
    """Whether this instance offers face grouping at all."""
    return bool(settings.PHOTOS_FACES_ENABLED)


def faces_enabled(user):
    """Whether *user* has face grouping on, on an instance that offers it."""
    return faces_available() and get_setting(user, MODULE, FACES_ENABLED) is True


def opted_in_owner_ids():
    """The users who turned face grouping on, as a subquery of ids.

    Read straight from the table: this feeds a queryset filter, where going
    through the per-user settings cache would cost one lookup per user.
    """
    return UserSetting.objects.filter(
        module=MODULE, key=FACES_ENABLED, value=True
    ).values("user_id")


def lock_opt_in(user_id):
    """Whether *user_id* has face grouping on, locking the setting until commit.

    For a write that must not outlive an opt-out: called inside the write's
    transaction, it holds a concurrent opt-out back until the write commits
    (PostgreSQL), or sees it if it committed first (SQLite serializes the
    two). The setting's cache is bypassed: a stale "on" is the one answer
    this must never give.
    """
    return (
        UserSetting.objects.select_for_update()
        .filter(user_id=user_id, module=MODULE, key=FACES_ENABLED, value=True)
        .exists()
    )
