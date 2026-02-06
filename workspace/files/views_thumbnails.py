"""Thumbnail management views."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


@extend_schema_view(post=extend_schema(tags=['Thumbnails']))
class GenerateThumbnailsView(APIView):
    """Trigger thumbnail generation for image files missing a thumbnail."""

    @extend_schema(
        summary="Trigger thumbnail generation",
        description="Manually trigger thumbnail generation for all image files missing a thumbnail.",
        responses={
            202: OpenApiResponse(description="Thumbnail generation task queued."),
        },
    )
    def post(self, request):
        from workspace.files.tasks import generate_thumbnails
        result = generate_thumbnails.delay()
        return Response({'task_id': result.id}, status=status.HTTP_202_ACCEPTED)
