"""Correcting the date and the place of a selection of photos and videos."""

from datetime import timedelta

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.users.services.settings import get_user_timezone

from ..services import capture_details

# A selection of a few long trips; the limit only bounds one request.
MAX_FILES = 2000
_MAX_SHIFT_MINUTES = int(capture_details.MAX_SHIFT / timedelta(minutes=1))


class CaptureDetailsSerializer(serializers.Serializer):
    uuids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=MAX_FILES
    )
    date_mode = serializers.ChoiceField(
        choices=["shift", "set", "restore"], required=False, allow_null=True
    )
    shift_minutes = serializers.IntegerField(
        required=False, min_value=-_MAX_SHIFT_MINUTES, max_value=_MAX_SHIFT_MINUTES
    )
    date = serializers.DateField(required=False)
    time = serializers.TimeField(required=False, allow_null=True)
    location_mode = serializers.ChoiceField(
        choices=["set", "clear", "restore"], required=False, allow_null=True
    )
    latitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, min_value=-180, max_value=180)

    def validate(self, attrs):
        date_mode = attrs.get("date_mode")
        location_mode = attrs.get("location_mode")
        if not date_mode and not location_mode:
            raise serializers.ValidationError("Nothing to change.")
        if date_mode == "shift" and not attrs.get("shift_minutes"):
            raise serializers.ValidationError(
                {"shift_minutes": "A shift needs a number of minutes other than 0."}
            )
        if date_mode == "set" and "date" not in attrs:
            raise serializers.ValidationError({"date": "This field is required."})
        if location_mode == "set":
            for field in ("latitude", "longitude"):
                if field not in attrs:
                    raise serializers.ValidationError(
                        {field: "This field is required."}
                    )
        return attrs


@extend_schema(tags=["Photos - Details"])
class CaptureDetailsView(APIView):
    @extend_schema(
        summary="Correct the date and place of photos",
        description=(
            "Change when and where photos and videos were taken, for everyone "
            "who can open them. `date_mode`: `shift` moves each date by "
            "`shift_minutes` (undated ones are skipped), `set` dates them on "
            "`date` at `time` in the caller's timezone (without a time, each "
            "keeps its own, undated ones land at noon), `restore` hands the "
            "date back to the file. `location_mode`: `set` places them at "
            "`latitude`, `longitude`, `clear` removes the place, `restore` "
            "hands it back to the file. Corrections survive the file being "
            "read again or renamed. Needs edit rights on every file; nothing "
            "changes when one of them is refused."
        ),
        request=CaptureDetailsSerializer,
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="How many photos had their date and place changed.",
            ),
            400: OpenApiResponse(description="Invalid request."),
            403: OpenApiResponse(description="A photo cannot be corrected."),
            404: OpenApiResponse(description="A photo was not found."),
        },
    )
    def post(self, request):
        serializer = CaptureDetailsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        uuids = set(data["uuids"])

        files = list(
            exclude_blocked(
                File.objects.filter(
                    FileService.accessible_files_q(request.user),
                    uuid__in=uuids,
                    media_item__isnull=False,
                )
            ).distinct()
        )
        if len(files) != len(uuids):
            return Response(
                {"detail": "One or more photos not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        permissions = FileService.get_permissions_bulk(request.user, files)
        if not all(
            capture_details.can_edit_capture_details(f, permissions[f.pk])
            for f in files
        ):
            return Response(
                {"detail": "One or more photos cannot be corrected."},
                status=status.HTTP_403_FORBIDDEN,
            )

        file_ids = [f.pk for f in files]
        with transaction.atomic():
            dated, located = _apply(request.user, file_ids, data)
        return Response({"dated": dated, "located": located})


def _apply(user, file_ids, data):
    """Write the corrections *data* asks for; returns how many photos had
    their date, and their place, changed."""
    dated = located = 0
    match data.get("date_mode"):
        case "shift":
            dated = capture_details.shift_taken_at(
                file_ids, timedelta(minutes=data["shift_minutes"])
            )
        case "set":
            dated = capture_details.set_taken_at(
                file_ids,
                data["date"],
                data.get("time"),
                get_user_timezone(user),
            )
        case "restore":
            dated = capture_details.restore_from_file(file_ids, taken_at=True)
    match data.get("location_mode"):
        case "set":
            located = capture_details.set_location(
                file_ids, data["latitude"], data["longitude"]
            )
        case "clear":
            located = capture_details.set_location(file_ids, None, None)
        case "restore":
            located = capture_details.restore_from_file(file_ids, location=True)
    return dated, located
