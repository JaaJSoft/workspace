"""Thumbnail management views."""

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy


@extend_schema_view(post=extend_schema(tags=["Thumbnails"]))
class GenerateThumbnailsView(APIView):
    """Trigger thumbnail generation for image files missing a thumbnail."""

    @extend_schema(
        summary="Trigger thumbnail generation",
        description=(
            "Manually trigger thumbnail generation for all image files missing "
            "a thumbnail. Set retry_failed to also retry files that were parked "
            "after repeated generation failures."
        ),
        request=inline_serializer(
            name="GenerateThumbnailsRequest",
            fields={
                "retry_failed": serializers.BooleanField(
                    required=False,
                    default=False,
                    help_text=(
                        "Staff only. Discards the recorded failures of every "
                        "file in the deployment, not just the caller's, so all "
                        "known-broken images are decoded again on this pass."
                    ),
                ),
            },
        ),
        responses={
            202: OpenApiResponse(description="Thumbnail generation task queued."),
            403: OpenApiResponse(
                description="retry_failed was requested by a non-staff caller."
            ),
        },
    )
    def post(self, request):
        from workspace.files.tasks import generate_thumbnails

        retry_failed = is_truthy(request.data.get("retry_failed"))
        if retry_failed and not request.user.is_staff:
            return Response(
                {"detail": "Retrying parked thumbnails is restricted to staff."},
                status=status.HTTP_403_FORBIDDEN,
            )
        result = generate_thumbnails.delay(retry_failed=retry_failed)
        return Response({"task_id": result.id}, status=status.HTTP_202_ACCEPTED)
