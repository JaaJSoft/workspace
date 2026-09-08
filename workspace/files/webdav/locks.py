"""DAV locks carried by the same ``File`` columns the in-app editors use.

wsgidav keeps its own lock table (token to url, principal and timeout), and
nothing in it reaches the browser: a note open in the markdown editor and a
mounted client would each believe they hold the file alone. Mirroring every
DAV lock onto ``locked_by`` / ``locked_at`` / ``lock_expires_at`` /
``lock_token`` puts both writers on the one lock the rest of the app already
reads - the same unification WOPI does in ``services/wopi/locks.py``, with the
DAV token playing the part of the WOPI lock id.

Only file resources are mirrored. A collection lock (``Depth: infinity``)
covers a subtree those four columns cannot express, and clients take one on
the mount root as a matter of course; the DAV lock table stays the authority
for those, as it was before.
"""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from wsgidav.dav_error import HTTP_LOCKED, DAVError
from wsgidav.lock_man.lock_manager import LockManager

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.locking import conflicting_lock

logger = logging.getLogger(__name__)


class AppLockManager(LockManager):
    """A wsgidav lock manager that keeps ``File.locked_by`` in step."""

    def acquire(self, *, url, principal, **kwargs):
        file_obj, user = _dav_file(url, principal)

        holder = conflicting_lock(file_obj, user)
        if holder is not None:
            raise DAVError(HTTP_LOCKED, f"File is locked by {holder.username}")

        lock = super().acquire(url=url, principal=principal, **kwargs)

        if file_obj is not None and not _bind(file_obj, user, lock):
            # Someone took the app lock between the check and the write. The
            # DAV lock is already granted, so give it back rather than leave a
            # token nothing on the app side knows about.
            super().release(lock["token"])
            raise DAVError(HTTP_LOCKED, "File is locked by another user.")
        return lock

    def refresh(self, token, *, timeout=None):
        lock = super().refresh(token, timeout=timeout)
        if lock:
            File.objects.filter(lock_token=lock["token"]).update(
                lock_expires_at=_expiry(lock)
            )
        return lock

    def release(self, token):
        # Read the row before the UPDATE clears the token it is found by, so
        # the editors watching this file can be told it is writable again -
        # the same ``lock_released`` the in-app endpoint pushes on DELETE.
        file_obj = (
            File.objects.filter(lock_token=token, deleted_at__isnull=True)
            .select_related("locked_by")
            .first()
        )
        super().release(token)
        cleared = File.objects.filter(lock_token=token).update(
            locked_by=None,
            locked_at=None,
            lock_expires_at=None,
            lock_token="",
        )
        if cleared and file_obj is not None:
            from workspace.files.sse_provider import push_file_event

            holder = getattr(file_obj.locked_by, "username", "")
            push_file_event(file_obj, "lock_released", scrub(holder))


def _dav_file(url, principal):
    """Resolve a DAV lock root to ``(File, User)``, either of which may be None.

    ``url`` is the resource's ref-url: quoted, absolute, and - for the provider
    mounted at ``/`` - the same tree path ``File.path`` stores.
    """
    user = get_user_model().objects.filter(username=principal).first()
    if user is None:
        return None, None
    path = unquote(url).strip("/")
    if not path:
        return None, user
    file_obj = (
        File.objects.filter(
            FileService.accessible_files_q(user),
            path=path,
            node_type=File.NodeType.FILE,
            deleted_at__isnull=True,
        )
        .select_related("locked_by")
        .first()
    )
    return file_obj, user


def _expiry(lock):
    """The lock's absolute expiry as a datetime.

    A wsgidav lock stores ``expire`` as an epoch, or a negative value for "no
    timeout". The app lock has no such state, so an untimed DAV lock is pinned
    to the storage's own ceiling instead of holding the file forever.
    """
    expire = float(lock.get("expire") or -1)
    if expire < 0:
        return timezone.now() + timedelta(seconds=LockManager.LOCK_TIME_OUT_DEFAULT)
    return datetime.fromtimestamp(expire, tz=UTC)


def _bind(file_obj, user, lock):
    """Write *lock* onto the File row. False when the row was taken meanwhile.

    The "is it still free?" predicate lives in the UPDATE's WHERE clause, like
    the app lock endpoint and the WOPI operations, so a concurrent acquire
    can't be silently overwritten.
    """
    now = timezone.now()
    bound = bool(
        File.objects.filter(pk=file_obj.pk)
        .filter(
            Q(locked_by__isnull=True) | Q(lock_expires_at__lte=now) | Q(locked_by=user),
        )
        .update(
            locked_by=user,
            locked_at=now,
            lock_expires_at=_expiry(lock),
            lock_token=lock["token"],
        )
    )
    if not bound:
        logger.info(
            "WebDAV LOCK lost the race for %s by %s",
            scrub(file_obj.path or file_obj.name),
            scrub(user.username),
        )
    return bound
