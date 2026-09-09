"""What decides whether a write may land on a file row.

Two questions, asked by every path that overwrites, renames, moves or deletes:
is the row locked by somebody else, and is the caller still writing over the
version they think they are?

``File.locked_by`` / ``lock_expires_at`` are the single lock the whole app
reads - the in-app editors take it through ``/api/v1/files/<uuid>/lock``, WOPI
carries it in ``services/wopi/locks.py``, and WebDAV mirrors its own tokens
onto it in ``webdav/locks.py``. ``content_hash`` is the version token: a caller
that sends the hash its buffer was loaded from is asking to be refused rather
than to overwrite somebody else's save.

The helpers here answer both questions against a row already in hand, which
makes them a *check*, not a guarantee - between the check and the write another
transaction can still move the row. Closing that window is the job of the
conditional UPDATE in ``FileService.update_content``, which re-asks the same
questions inside the write itself.
"""


class StaleContent(Exception):
    """A write was refused because the blob moved since the caller read it.

    Carries the hash that is actually stored, so the caller can tell the user
    what they are up against - and so a client can resynchronise without a
    second round trip.
    """

    def __init__(self, current_hash=""):
        self.current_hash = current_hash
        super().__init__("The file changed since it was loaded.")


def conflicting_lock(file_obj, user):
    """Return the lock holder fencing *user* out of *file_obj*, or ``None``.

    ``None`` covers the three cases a write may proceed under: no lock, an
    expired one, and a lock the caller holds themselves.
    """
    if file_obj is None or not file_obj.is_locked():
        return None
    if user is not None and file_obj.locked_by_id == user.pk:
        return None
    return file_obj.locked_by


def unlocked_for_q(user):
    """Q matching the rows *user* may write: free, expired, or already theirs.

    The predicate form of ``conflicting_lock``, for the WHERE clause of a
    conditional write. The two must agree - one of them decides the response
    code, the other decides whether the row actually changes.
    """
    from django.db.models import Q
    from django.utils import timezone

    return (
        Q(locked_by__isnull=True)
        | Q(lock_expires_at__lte=timezone.now())
        | Q(locked_by=user)
    )


def precondition_hash(request):
    """The ``content_hash`` *request* expects to be overwriting, or ``""``.

    Accepted as an ``If-Match`` header (quoted or bare) or a ``base_hash``
    part, so a browser save carrying multipart form data and a scripted PUT
    can both express it. ``""`` means the caller sent no precondition and is
    content with last-write-wins; ``*`` is the standard "any version".
    """
    supplied = request.headers.get("If-Match")
    if not supplied and hasattr(request.data, "get"):
        supplied = request.data.get("base_hash")
    supplied = (supplied or "").strip().strip('"')
    return "" if supplied == "*" else supplied
