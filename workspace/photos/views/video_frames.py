"""The picture of a video's key frame: what a ranking shows of the moment of
the video that matched (services/photo_embeddings.py)."""

from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from ..models import PhotoEmbedding
from ..queries import ALL, library_files
from ..services.embedding.registry import embeddings_available


@extend_schema(tags=["Photos - Search"])
class VideoFrameView(CacheControlMixin, APIView):
    """A key frame of a video the caller can open, as a WebP.

    A frame never changes (new bytes make new frames), so the browser may
    keep it; private, as the video is.
    """

    cache_max_age = 7 * 24 * 3600

    @extend_schema(
        summary="Get a video key frame",
        responses={200: OpenApiResponse(description="WebP image"), 404: None},
    )
    def get(self, request, pk):
        if not embeddings_available():
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)
        frame = (
            PhotoEmbedding.objects.filter(
                pk=pk, file__in=library_files(request.user, ALL)
            )
            .values_list("frame", flat=True)
            .first()
        )
        if not frame:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)
        try:
            handle = default_storage.open(frame, "rb")
        except FileNotFoundError:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)
        return FileResponse(handle, content_type="image/webp")
