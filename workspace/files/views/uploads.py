"""Resumable uploads: a file sent in chunks over several requests.

    POST   /api/v1/files/uploads                 open a session
    GET    /api/v1/files/uploads/<uuid>          where to resume from
    PATCH  /api/v1/files/uploads/<uuid>          append a chunk (Content-Range)
    POST   /api/v1/files/uploads/<uuid>/finalize store the file
    DELETE /api/v1/files/uploads/<uuid>          abort

Finalizing answers like ``POST /api/v1/files`` does for the same upload, so
the client handles both the same way.
"""

import io
import re

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.files.services import FileService, uploads
from workspace.files.services.quota import check_write_allowed

from ..models import File, UploadSession
from ..serializers import (
    FileSerializer,
    UploadSessionCreateSerializer,
    UploadSessionSerializer,
    content_write_refusals,
    ensure_replaceable,
    name_refusals,
    name_taken_error,
)
from ..services._names import NameTaken
from .files import announce_replaced, duplicate_entries

_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")

FOLDER_GONE = "The folder this upload goes to no longer exists."


def _offset_response(detail, offset, code):
    return Response({"detail": detail, "offset": offset}, status=code)


def _writable_folder(user, parent):
    """*parent* when *user* may add files to it, else None."""
    if parent is None or not FileService.can_create_in(user, parent):
        return None
    return parent


def _session_or_none(user, uuid):
    return UploadSession.objects.filter(pk=uuid, owner=user).first()


def _not_found():
    return Response(
        {"detail": "No such upload session."}, status=status.HTTP_404_NOT_FOUND
    )


@extend_schema(tags=["Files"])
class UploadSessionListView(APIView):
    @extend_schema(
        summary="Open a resumable upload",
        description=(
            "Start an upload sent in chunks, which survives a network cut: the "
            "client asks where to resume from and sends the rest. The declared "
            "size counts against the storage quota from now until the upload is "
            "finalized or aborted. A name taken in the target folder is refused "
            "now rather than after the transfer, unless on_conflict says what to "
            "do with it."
        ),
        request=UploadSessionCreateSerializer,
        responses={
            201: UploadSessionSerializer,
            400: OpenApiResponse(description="Invalid request, or name taken."),
            403: OpenApiResponse(description="The file to replace cannot be."),
            413: OpenApiResponse(description="Storage quota exceeded."),
            423: OpenApiResponse(description="The file to replace is locked."),
        },
    )
    def post(self, request):
        serializer = UploadSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = request.user

        parent = None
        if data.get("parent") is not None:
            parent = _writable_folder(
                user, File.objects.filter(uuid=data["parent"]).first()
            )
            if parent is None:
                raise ValidationError({"parent": ["Folder not found."]})

        # Settled now when it can be, so a refused name costs no transfer.
        # Finalizing decides again: the folder can change in between.
        name, on_conflict = data["name"], data["on_conflict"]
        if on_conflict == UploadSession.OnConflict.ERROR:
            try:
                FileService.check_name_available(user, parent, name, File.NodeType.FILE)
            except NameTaken as error:
                raise name_taken_error(error) from error
        elif on_conflict == UploadSession.OnConflict.REPLACE:
            target = FileService.find_name_conflict(user, parent, name)
            if target is not None:
                ensure_replaceable(user, target)
            else:
                try:
                    FileService.check_name_available(
                        user, parent, name, File.NodeType.FILE
                    )
                except NameTaken as error:
                    raise name_taken_error(error) from error

        try:
            session, created = uploads.open_session(
                user,
                name=name,
                parent=parent,
                size=data["size"],
                on_conflict=on_conflict,
                mime_type=data.get("mime_type", ""),
                uuid=data.get("uuid"),
            )
        except uploads.SessionTaken as error:
            raise ValidationError(
                {"uuid": ["This identifier is already in use."]}
            ) from error
        return Response(
            UploadSessionSerializer(session).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Files"])
class UploadSessionView(APIView):
    @extend_schema(
        summary="Read a resumable upload's progress",
        description="`offset` is where the next chunk starts.",
        responses={200: UploadSessionSerializer, 404: OpenApiResponse()},
    )
    def get(self, request, uuid):
        session = _session_or_none(request.user, uuid)
        if session is None:
            return _not_found()
        return Response(UploadSessionSerializer(session).data)

    @extend_schema(
        summary="Append a chunk to a resumable upload",
        description=(
            "The body is the raw bytes of the chunk, and `Content-Range: bytes "
            "<first>-<last>/<size>` says where they go. Chunks go in order: one "
            "that does not start at the current offset is refused with 409 and "
            "the offset to resume from, and so is one sent while another request "
            "is still writing. When the body ends early, the bytes that arrived "
            "are kept and the answer is a 400 carrying the new offset."
        ),
        parameters=[
            OpenApiParameter(
                "Content-Range",
                OpenApiTypes.STR,
                OpenApiParameter.HEADER,
                required=True,
                description="bytes <first>-<last>/<size>",
            )
        ],
        request={"application/octet-stream": OpenApiTypes.BINARY},
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT, description="`{offset}` after the chunk."
            ),
            400: OpenApiResponse(description="Invalid range, or a cut chunk."),
            404: OpenApiResponse(description="No such session, or it ended."),
            409: OpenApiResponse(description="Out of order, or busy: `{offset}`."),
            413: OpenApiResponse(description="Chunk larger than allowed."),
        },
    )
    def patch(self, request, uuid):
        session = _session_or_none(request.user, uuid)
        if session is None or session.completed_at is not None:
            return _not_found()

        match = _CONTENT_RANGE.match(request.headers.get("Content-Range", ""))
        if match is None:
            return Response(
                {"detail": "Content-Range must read 'bytes <first>-<last>/<size>'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        first, last, total = match.groups()
        start, length = int(first), int(last) - int(first) + 1
        if total != "*" and int(total) != session.size:
            return Response(
                {"detail": "Content-Range does not match the declared size."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if length <= 0 or start + length > session.size:
            return Response(
                {"detail": "The chunk does not fit in the declared size."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if length > uploads.MAX_CHUNK_SIZE:
            return Response(
                {"detail": f"Chunks are {uploads.MAX_CHUNK_SIZE} bytes at most."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        if request.headers.get("Content-Length") not in (None, str(length)):
            return Response(
                {"detail": "Content-Length does not match Content-Range."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # No stream at all is a body that ended before its first byte.
            body = request.stream or io.BytesIO()
            offset = uploads.append(session, start, body, length)
        except uploads.SessionGone:
            return _not_found()
        except uploads.OffsetMismatch as exc:
            return _offset_response(
                "The upload continues elsewhere.", exc.offset, status.HTTP_409_CONFLICT
            )
        except uploads.SessionBusy:
            session.refresh_from_db(fields=["received"])
            return _offset_response(
                "Another request is writing to this upload.",
                session.received,
                status.HTTP_409_CONFLICT,
            )
        except uploads.ChunkIncomplete as exc:
            return _offset_response(
                "The chunk ended early.", exc.offset, status.HTTP_400_BAD_REQUEST
            )
        return Response({"offset": offset})

    @extend_schema(
        summary="Abort a resumable upload",
        description="Drops what it received and releases its quota reservation.",
        responses={204: OpenApiResponse(), 404: OpenApiResponse()},
    )
    def delete(self, request, uuid):
        session = _session_or_none(request.user, uuid)
        if session is None:
            return _not_found()
        uploads.abort(session)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Files"])
class UploadSessionFinalizeView(APIView):
    @extend_schema(
        summary="Finalize a resumable upload",
        description=(
            "Store the received file in its folder, resolving a taken name the "
            "way the session's on_conflict asks. Answers as `POST /api/v1/files` "
            "does: 201 with the new file and its `duplicates`, or 200 when it "
            "replaced an existing file. Finalizing again answers the same, so a "
            "client whose answer was lost can ask again."
        ),
        request=None,
        responses={
            200: FileSerializer,
            201: FileSerializer,
            400: OpenApiResponse(description="Name taken, or folder gone."),
            403: OpenApiResponse(description="The file to replace cannot be."),
            404: OpenApiResponse(description="No such session."),
            409: OpenApiResponse(description="Bytes missing, or busy: `{offset}`."),
            413: OpenApiResponse(description="Storage quota exceeded."),
            423: OpenApiResponse(description="The file to replace is locked."),
        },
    )
    def post(self, request, uuid):
        user = request.user
        session = _session_or_none(user, uuid)
        if session is None:
            return _not_found()
        if session.completed_at is not None:
            return self._answer(request, session.file, replaced=session.replaced)

        parent = None
        if session.parent_id is not None:
            parent = _writable_folder(user, session.parent)
            if parent is None:
                raise ValidationError({"parent": [FOLDER_GONE]})

        try:
            with uploads.finished_upload(session) as content:
                file_obj, replaced = self._store(session, parent, content, user)
                uploads.complete(session, file_obj, replaced=replaced)
        except uploads.SessionGone:
            # A finalize that ran while this request was reading the session
            # stored the file already: answer with it.
            session = _session_or_none(user, uuid)
            if session is None or session.completed_at is None:
                return _not_found()
            return self._answer(request, session.file, replaced=session.replaced)
        except uploads.UploadIncomplete as exc:
            return _offset_response(
                "Bytes are still missing.", exc.offset, status.HTTP_409_CONFLICT
            )
        except uploads.SessionBusy:
            session.refresh_from_db(fields=["received"])
            return _offset_response(
                "Another request is writing to this upload.",
                session.received,
                status.HTTP_409_CONFLICT,
            )
        uploads.release(session)
        if replaced:
            announce_replaced(file_obj, user)
        return self._answer(request, file_obj, replaced=replaced)

    @staticmethod
    def _store(session, parent, content, user):
        """Create or replace the file; return ``(file, replaced)``.

        The quota was checked against the declared size when the session
        opened, and every write since has counted it as reserved; the check
        here leaves that reservation out, since these are its bytes.
        """
        target = None
        if session.on_conflict == UploadSession.OnConflict.REPLACE:
            target = FileService.find_name_conflict(user, parent, session.name)
        if target is not None:
            ensure_replaceable(user, target)
            check_write_allowed(
                owner=target.owner_id,
                group=target.group_id,
                additional_bytes=content.size - (target.size or 0),
                excluding=session,
            )
            with content_write_refusals():
                FileService.update_content(
                    target,
                    content,
                    name=target.name,
                    acting_user=user,
                    check_quota=False,
                )
            return target, True

        check_write_allowed(
            owner=user,
            group=session.group_id,
            additional_bytes=content.size,
            excluding=session,
        )
        with name_refusals():
            file_obj = FileService.create_file(
                owner=user,
                name=session.name,
                parent=parent,
                content=content,
                acting_user=user,
                check_quota=False,
                keep_both=session.on_conflict == UploadSession.OnConflict.RENAME,
            )
        return file_obj, False

    @staticmethod
    def _answer(request, file_obj, *, replaced):
        instance = None
        if file_obj is not None:
            instance = FileService.annotate_for_serializer(
                File.objects.filter(pk=file_obj.pk, deleted_at__isnull=True),
                request.user,
            ).first()
        if instance is None:
            return Response(
                {"detail": "The uploaded file no longer exists."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = FileSerializer(instance, context={"request": request}).data
        data["duplicates"] = duplicate_entries(instance)
        return Response(
            data, status=status.HTTP_200_OK if replaced else status.HTTP_201_CREATED
        )
