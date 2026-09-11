"""WOPI host endpoints, called by the office editor - never by the browser.

Routes (WOPI fixes the shape; the editor derives the /contents URL itself):

    GET  /api/wopi/files/<uuid>            CheckFileInfo
    POST /api/wopi/files/<uuid>            Lock operations (X-WOPI-Override)
    GET  /api/wopi/files/<uuid>/contents   GetFile
    POST /api/wopi/files/<uuid>/contents   PutFile

``X-WOPI-ItemVersion`` is the file's ``content_hash``, so an editor that sends
the version it opened back as ``If-Match`` on PutFile gets its save refused
with 409 rather than silently overwriting somebody else's - the same
precondition the browser editors carry, spelled the way WOPI already spells
versions.

Authentication is the ``access_token`` query parameter mandated by the
protocol - no session, no CSRF. The token pins a user and a file; the user's
permission is re-checked against the live ACL on every request, so revoking a
share cuts running sessions off at the next editor round-trip.
"""

import logging

from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import FilePermission, FileService
from workspace.files.services.locking import (
    LockConflict,
    StaleContent,
    precondition_hash,
)
from workspace.files.services.quota import QuotaExceeded
from workspace.files.services.wopi import locks
from workspace.files.services.wopi.tokens import parse_access_token

logger = logging.getLogger(__name__)


@sensitive_variables("token")
def _authenticate(request, uuid):
    """(file, user, can_write) for a valid token, or an error response.

    ``can_write`` is the AND of what the token was minted with and what the
    ACL says right now - a token never upgrades a permission, and a demoted
    user degrades to read-only mid-session.
    """
    token = request.GET.get("access_token", "")
    if not token:
        return HttpResponse(status=401)
    parsed = parse_access_token(token, uuid)
    if parsed is None:
        return HttpResponse(status=401)
    user, token_can_write = parsed
    file_obj = (
        File.objects.select_related("locked_by", "owner")
        .filter(uuid=uuid, deleted_at__isnull=True, node_type=File.NodeType.FILE)
        .first()
    )
    if file_obj is None:
        raise Http404
    perm = FileService.get_permission(user, file_obj)
    if perm is None:
        raise Http404
    can_write = token_can_write and perm >= FilePermission.WRITE
    return file_obj, user, can_write


def _item_version(file_obj) -> str:
    return file_obj.content_hash or str(int(file_obj.updated_at.timestamp()))


def _expected_version(request, file_obj):
    """The ``content_hash`` an ``If-Match`` on PutFile is asking to overwrite.

    ``_item_version`` falls back to a timestamp on a row the hash backfill has
    not reached, so an editor that sends that version back is naming the row's
    actual version - the empty hash - and not a hash of its own. Without the
    translation the precondition could never be satisfied and every save on
    such a file would answer 409.
    """
    supplied = precondition_hash(request)
    if supplied is not None and not file_obj.content_hash:
        return "" if supplied == _item_version(file_obj) else supplied
    return supplied


def _lock_conflict(outcome) -> HttpResponse:
    response = HttpResponse(status=409)
    response["X-WOPI-Lock"] = outcome.current_lock
    return response


@method_decorator(csrf_exempt, name="dispatch")
class WopiFileView(View):
    """CheckFileInfo (GET) and lock operations (POST)."""

    def get(self, request, uuid):
        auth = _authenticate(request, uuid)
        if isinstance(auth, HttpResponse):
            return auth
        file_obj, user, can_write = auth
        return JsonResponse(
            {
                "BaseFileName": file_obj.name,
                "Size": file_obj.size or 0,
                "Version": _item_version(file_obj),
                "OwnerId": str(file_obj.owner_id),
                "UserId": str(user.pk),
                "UserFriendlyName": user.get_full_name() or user.username,
                "UserCanWrite": can_write,
                "ReadOnly": not can_write,
                "UserCanRename": False,
                "UserCanNotWriteRelative": True,
                "SupportsUpdate": True,
                "SupportsLocks": True,
                "SupportsGetLock": True,
                "SupportsExtendedLockLength": True,
                "LastModifiedTime": file_obj.updated_at.isoformat(),
            }
        )

    def post(self, request, uuid):
        auth = _authenticate(request, uuid)
        if isinstance(auth, HttpResponse):
            return auth
        file_obj, user, can_write = auth

        override = request.headers.get("X-WOPI-Override", "")
        lock_id = request.headers.get("X-WOPI-Lock", "")

        if override == "GET_LOCK":
            response = HttpResponse(status=200)
            response["X-WOPI-Lock"] = locks.current_lock_token(file_obj)
            return response

        if not can_write:
            return HttpResponse(status=401)
        if not lock_id:
            return HttpResponse(status=400)

        if override == "LOCK":
            # An X-WOPI-OldLock turns LOCK into UnlockAndRelock.
            old_lock = request.headers.get("X-WOPI-OldLock", "")
            outcome = locks.lock(file_obj, user, lock_id, old_lock_id=old_lock)
        elif override == "REFRESH_LOCK":
            outcome = locks.refresh(file_obj, lock_id)
        elif override == "UNLOCK":
            outcome = locks.unlock(file_obj, lock_id)
        else:
            return HttpResponse(status=501)

        if not outcome.ok:
            return _lock_conflict(outcome)
        response = HttpResponse(status=200)
        response["X-WOPI-ItemVersion"] = _item_version(file_obj)
        return response


@method_decorator(csrf_exempt, name="dispatch")
class WopiFileContentsView(View):
    """GetFile (GET) and PutFile (POST)."""

    def get(self, request, uuid):
        auth = _authenticate(request, uuid)
        if isinstance(auth, HttpResponse):
            return auth
        file_obj, _user, _can_write = auth
        from workspace.files.services.scanning.policy import blocked_reason

        # WOPI has no "blocked" outcome; 404 is what Collabora handles
        # gracefully, and it is what a hard-deleted file already returns.
        if blocked_reason(file_obj) is not None:
            raise Http404
        try:
            stream = file_obj.content.open("rb")
        except FileNotFoundError, OSError:
            logger.warning("WOPI GetFile missing blob for %s", scrub(str(uuid)))
            raise Http404 from None
        response = FileResponse(stream, content_type="application/octet-stream")
        response["X-WOPI-ItemVersion"] = _item_version(file_obj)
        return response

    def post(self, request, uuid):
        auth = _authenticate(request, uuid)
        if isinstance(auth, HttpResponse):
            return auth
        file_obj, user, can_write = auth
        if not can_write:
            return HttpResponse(status=401)
        if request.headers.get("X-WOPI-Override", "PUT") != "PUT":
            return HttpResponse(status=501)

        lock_id = request.headers.get("X-WOPI-Lock", "")
        outcome = locks.put_allowed(file_obj, user, lock_id)
        if not outcome.ok:
            return _lock_conflict(outcome)

        content = ContentFile(request.body, name=file_obj.name)
        try:
            FileService.update_content(
                file_obj,
                content,
                acting_user=user,
                expected_hash=_expected_version(request, file_obj),
            )
        except QuotaExceeded as exc:
            # A plain Django view: nothing translates a DRF exception here, so
            # an escaping QuotaExceeded would reach the editor as a 500.
            return HttpResponse(
                str(exc.detail), status=413, content_type="text/plain; charset=utf-8"
            )
        except LockConflict:
            # Somebody took the app lock while the editor was saving. The WOPI
            # lock it holds is unrelated to that one, so there is no lock id to
            # hand back - 409 with no X-WOPI-Lock is the protocol's "try again".
            return HttpResponse(status=409)
        except StaleContent as exc:
            # The editor asked to be refused if the file moved, and it did. The
            # version it is now up against goes back in the same header the
            # editor reads everywhere else - which means re-reading the row when
            # the stored hash is empty, since the header falls back to a
            # timestamp there.
            current = exc.current_hash
            if not current:
                moved = File.objects.filter(pk=file_obj.pk).first()
                current = _item_version(moved) if moved else ""
            response = HttpResponse(status=409)
            response["X-WOPI-ItemVersion"] = current
            return response
        response = JsonResponse({"LastModifiedTime": file_obj.updated_at.isoformat()})
        response["X-WOPI-ItemVersion"] = _item_version(file_obj)
        return response
