"""WOPI lock operations mapped onto File.locked_by / lock_expires_at.

A WOPI lock is an opaque string the editor supplies; the host stores it and
answers conflicts with the current value in the ``X-WOPI-Lock`` header. It is
carried by the same three lock columns the in-app editors use, plus
``wopi_lock`` for the string itself - so a WOPI editing session looks locked
to the rest of the app, and an app lock (empty ``wopi_lock``) blocks WOPI
operations symmetrically.

Every mutation runs its "is the lock in the expected state?" predicate inside
the UPDATE's WHERE clause, mirroring the app-lock endpoint: two concurrent
operations can't both win, the loser reads back the row and reports 409.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from ...models import File

# WOPI locks expire after 30 minutes unless refreshed (protocol constant).
WOPI_LOCK_DURATION = timedelta(minutes=30)


@dataclass(frozen=True)
class LockOutcome:
    ok: bool
    # Current lock value to expose in X-WOPI-Lock on conflict. An app lock has
    # no WOPI lock id, so a conflict against one reports the empty string.
    current_lock: str = ""


def _expired_q(now):
    return Q(lock_expires_at__isnull=True) | Q(lock_expires_at__lte=now)


def current_wopi_lock(file_obj) -> str:
    """Active WOPI lock string, or '' when unlocked / app-locked / expired."""
    file_obj.refresh_from_db(
        fields=["wopi_lock", "locked_by", "lock_expires_at", "locked_at"]
    )
    if not file_obj.wopi_lock or not file_obj.is_locked():
        return ""
    return file_obj.wopi_lock


def has_app_lock_conflict(file_obj, user) -> bool:
    """True when another user's non-WOPI lock is active on the file."""
    return (
        file_obj.is_locked()
        and not file_obj.wopi_lock
        and file_obj.locked_by_id != user.pk
    )


def _conflict(file_obj) -> LockOutcome:
    return LockOutcome(ok=False, current_lock=current_wopi_lock(file_obj))


def lock(file_obj, user, lock_id: str, old_lock_id: str = "") -> LockOutcome:
    """LOCK, or UNLOCK_AND_RELOCK when *old_lock_id* is given."""
    now = timezone.now()
    if old_lock_id:
        expected = Q(wopi_lock=old_lock_id) & Q(lock_expires_at__gt=now)
    else:
        # Free, expired, refreshing the same lock, or upgrading the caller's
        # own app lock (the in-app viewer acquires one before the WOPI frame
        # loads; it must not fence out its own editing session).
        expected = (
            Q(locked_by__isnull=True)
            | _expired_q(now)
            | Q(wopi_lock=lock_id)
            | (Q(wopi_lock="") & Q(locked_by=user))
        )
    updated = (
        File.objects.filter(pk=file_obj.pk)
        .filter(expected)
        .update(
            wopi_lock=lock_id,
            locked_by=user,
            locked_at=now,
            lock_expires_at=now + WOPI_LOCK_DURATION,
        )
    )
    if not updated:
        return _conflict(file_obj)
    return LockOutcome(ok=True)


def unlock(file_obj, lock_id: str) -> LockOutcome:
    now = timezone.now()
    updated = (
        File.objects.filter(pk=file_obj.pk, wopi_lock=lock_id)
        .filter(Q(lock_expires_at__gt=now))
        .update(
            wopi_lock="",
            locked_by=None,
            locked_at=None,
            lock_expires_at=None,
        )
    )
    if not updated:
        return _conflict(file_obj)
    return LockOutcome(ok=True)


def refresh(file_obj, lock_id: str) -> LockOutcome:
    now = timezone.now()
    updated = (
        File.objects.filter(pk=file_obj.pk, wopi_lock=lock_id)
        .filter(Q(lock_expires_at__gt=now))
        .update(lock_expires_at=now + WOPI_LOCK_DURATION)
    )
    if not updated:
        return _conflict(file_obj)
    return LockOutcome(ok=True)


def put_allowed(file_obj, user, lock_id: str) -> LockOutcome:
    """Whether a PutFile carrying *lock_id* (possibly empty) may write.

    The WOPI spec wants an unlocked, non-empty file to refuse PutFile - but
    Collabora never issues WOPI locks, so enforcing that would reject every
    save it makes. Deviation: an unlocked file accepts the write; only an
    actively held mismatching lock (WOPI or another user's app lock) refuses.
    """
    active_wopi = current_wopi_lock(file_obj)
    if active_wopi:
        if lock_id == active_wopi:
            return LockOutcome(ok=True)
        return LockOutcome(ok=False, current_lock=active_wopi)
    if has_app_lock_conflict(file_obj, user):
        return LockOutcome(ok=False, current_lock="")
    return LockOutcome(ok=True)
