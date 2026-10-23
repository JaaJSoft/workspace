"""The people found by their body: their crops, and the answers to who they are."""

from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from ..queries import user_appearances
from ..serializers import AppearanceBatchSerializer, FaceUndoSerializer
from ..services import appearance_review
from .faces import _person, _require_faces


@extend_schema(tags=["Photos - Faces"])
class AppearanceCropView(CacheControlMixin, APIView):
    """The WebP of the torso of a person found by their body.

    Never changes (a new analysis makes new rows), so the browser may keep
    it; private, as it is somebody's picture.
    """

    cache_max_age = 7 * 24 * 3600

    @extend_schema(
        summary="Get the crop of a person found by their body",
        responses={200: OpenApiResponse(description="WebP image"), 404: None},
    )
    def get(self, request, pk):
        _require_faces()
        crop = (
            user_appearances(request.user)
            .filter(pk=pk)
            .values_list("crop", flat=True)
            .first()
        )
        if not crop:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)
        try:
            handle = default_storage.open(crop, "rb")
        except FileNotFoundError:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)
        return FileResponse(handle, content_type="image/webp")


@extend_schema(tags=["Photos - Faces"])
class AppearanceBatchView(APIView):
    """One answer for several people found by their body."""

    @extend_schema(
        summary="Say who several people found by their body are",
        description=(
            "confirm: they are the person their clothes suggest; reject: they "
            "are not the person named - the one suggested, the one they were "
            "said to be, or the one the review asked about (both skip whoever "
            "is suggested or said to be someone else by now); hide: nobody to "
            "name; assign: they are this contact. "
            "Each one that cannot take the answer is skipped with a reason "
            "('unavailable', 'already_in_photo', 'no_face', 'missing'). A "
            "confirm or assign naming someone another person of the photo "
            "already is goes through when their boxes overlap - the same person "
            "boxed twice, hidden as nobody new to ask about - and is skipped as "
            "'already_in_photo' otherwise, still rejecting the suggested person, "
            "or the one named without a suggestion, and hiding them, so they are "
            "not asked about again. The "
            "returned undo token puts them back as they were, for a few minutes."
        ),
        request=AppearanceBatchSerializer,
        responses={200: OpenApiResponse(description="done, skipped, undo")},
    )
    def post(self, request):
        _require_faces()
        serializer = AppearanceBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        person = (
            _person(request.user, data["person"], "person")
            if data.get("person")
            else None
        )
        wanted = list(dict.fromkeys(data["appearances"]))
        rows = {
            row.pk: row for row in user_appearances(request.user).filter(pk__in=wanted)
        }
        result = appearance_review.apply(
            request.user,
            data["action"],
            [rows[pk] for pk in wanted if pk in rows],
            person=person,
        )
        skipped = [
            {"appearance": str(pk), "reason": reason} for pk, reason in result.skipped
        ] + [
            {"appearance": str(pk), "reason": "missing"}
            for pk in wanted
            if pk not in rows
        ]
        return Response(
            {
                "done": [str(pk) for pk in result.done],
                "skipped": skipped,
                "undo": result.undo,
            }
        )


@extend_schema(tags=["Photos - Faces"])
class AppearanceUndoView(APIView):
    @extend_schema(
        summary="Undo an answer for several people found by their body",
        request=FaceUndoSerializer,
        responses={
            200: OpenApiResponse(description="restored: how many went back"),
            410: OpenApiResponse(description="Too late to undo."),
        },
    )
    def post(self, request):
        _require_faces()
        serializer = FaceUndoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            restored = appearance_review.undo(
                request.user, serializer.validated_data["token"]
            )
        except appearance_review.UndoExpired:
            return Response(
                {"detail": "Too late to undo."}, status=status.HTTP_410_GONE
            )
        return Response({"restored": restored})
