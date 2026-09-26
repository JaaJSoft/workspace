"""The face grouping API, under /api/v1/photos.

Every endpoint is a 404 on an instance without face grouping. For a user who
has it off, lists are empty and single faces or clusters are not found: the
rows a pending purge has not reached yet are not theirs to see any more.
"""

from django.core.files.storage import default_storage
from django.db.models import F
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.mixins import CacheControlMixin
from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.files.serializers import FileSerializer
from workspace.files.services import FileService

from ..queries import (
    cluster_photos,
    face_progress,
    user_face_clusters,
    user_faces,
)
from ..serializers import (
    FaceClusterCreateSerializer,
    FaceClusterMergeSerializer,
    FaceClusterSerializer,
    FaceSerializer,
)
from ..services import face_corrections
from ..services.face_preferences import faces_available, faces_enabled


def _require_faces():
    if not faces_available():
        raise NotFound


def _correction(fn, *args):
    # Literal messages, never the exception's own text: what reaches the
    # client is decided here.
    try:
        fn(*args)
    except face_corrections.PhotoAlreadyInCluster:
        raise ValidationError(
            {"detail": "Another face of this photo is already in that group."}
        ) from None
    except face_corrections.CoverNotInCluster:
        raise ValidationError(
            {"detail": "The cover must be one of the group's faces."}
        ) from None


@extend_schema(tags=["Photos - Faces"])
class FaceClusterViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """The user's face clusters: people the grouping found in their photos.

    DELETE ungroups: the cluster goes, and its faces stay out of automatic
    grouping until the user places them.
    """

    serializer_class = FaceClusterSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = OptInLimitOffsetPagination
    lookup_value_regex = "[0-9a-fA-F-]{36}"
    http_method_names = ["get", "patch", "post", "delete"]

    def get_queryset(self):
        _require_faces()
        clusters = user_face_clusters(self.request.user)
        if self.action == "list":
            clusters = clusters.filter(
                hidden=is_truthy(self.request.query_params.get("hidden"))
            )
        return clusters.order_by("-photo_count", "-face_count", "created_at")

    @extend_schema(
        summary="List face clusters",
        parameters=[
            OpenApiParameter(
                "hidden",
                bool,
                description="List the hidden clusters instead of the visible ones.",
            )
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Start a cluster from a face",
        description=(
            "The face is someone who has no cluster yet: it moves to a new "
            "one, confirmed."
        ),
        request=FaceClusterCreateSerializer,
        responses={201: FaceClusterSerializer},
    )
    def create(self, request):
        _require_faces()
        serializer = FaceClusterCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        face = (
            user_faces(request.user)
            .filter(pk=serializer.validated_data["face"])
            .first()
        )
        if face is None:
            raise ValidationError({"face": "No such face."})
        cluster = face_corrections.start_cluster(face)
        created = user_face_clusters(request.user).get(pk=cluster.pk)
        return Response(
            self.get_serializer(created).data, status=status.HTTP_201_CREATED
        )

    def perform_update(self, serializer):
        cluster = serializer.instance
        data = serializer.validated_data
        if "hidden" in data:
            face_corrections.set_hidden(cluster, data["hidden"])
        if "cover" in data:
            _correction(face_corrections.set_cover, cluster, data["cover"])

    def perform_destroy(self, instance):
        face_corrections.ungroup(instance)

    @extend_schema(
        summary="Merge clusters into this one",
        request=FaceClusterMergeSerializer,
        responses={200: FaceClusterSerializer},
    )
    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        target = self.get_object()
        serializer = FaceClusterMergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sources = list(
            user_face_clusters(request.user).filter(
                pk__in=serializer.validated_data["clusters"]
            )
        )
        if len(sources) != len(set(serializer.validated_data["clusters"])):
            raise NotFound
        face_corrections.merge_clusters(target, sources)
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(
        summary="Photos of a cluster",
        description=(
            "Newest capture first. Paginated with ?limit and ?offset; the "
            "X-Has-More header says whether another page follows."
        ),
        responses={200: FileSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def photos(self, request, pk=None):
        cluster = self.get_object()
        files = FileService.annotate_for_serializer(
            cluster_photos(request.user, cluster), request.user
        ).order_by(F("media_item__taken_at").desc(nulls_last=True), "-created_at")
        paginator = OptInLimitOffsetPagination()
        page = paginator.paginate_queryset(files, request, view=self)
        context = {"request": request}
        if page is None:
            return Response(FileSerializer(files, many=True, context=context).data)
        return paginator.get_paginated_response(
            FileSerializer(page, many=True, context=context).data
        )


@extend_schema(tags=["Photos - Faces"])
class FaceViewSet(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    """One face found in one of the user's photos, and its corrections."""

    serializer_class = FaceSerializer
    permission_classes = [IsAuthenticated]
    lookup_value_regex = "[0-9a-fA-F-]{36}"
    http_method_names = ["get", "patch"]

    def get_queryset(self):
        _require_faces()
        return user_faces(self.request.user)

    @extend_schema(summary="Correct a face's cluster")
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        face = serializer.instance
        data = serializer.validated_data
        if "cluster_id" in data:
            if data["cluster_id"] is None:
                face_corrections.reject_face(face)
                return
            cluster = (
                user_face_clusters(self.request.user)
                .filter(pk=data["cluster_id"])
                .first()
            )
            if cluster is None:
                raise ValidationError({"cluster": "No such cluster."})
            _correction(face_corrections.confirm_face, face, cluster)
        elif data.get("assignment") == face.Assignment.CONFIRMED:
            if face.cluster is None:
                raise ValidationError({"assignment": "The face is in no cluster."})
            _correction(face_corrections.confirm_face, face, face.cluster)


@extend_schema(tags=["Photos - Faces"])
class FaceCropView(CacheControlMixin, APIView):
    """The square WebP of a face.

    A face's crop never changes (a new analysis makes new faces), so the
    browser may keep it; private, as it is somebody's face.
    """

    permission_classes = [IsAuthenticated]
    cache_max_age = 7 * 24 * 3600

    @extend_schema(
        summary="Get a face crop",
        responses={200: OpenApiResponse(description="WebP image"), 404: None},
    )
    def get(self, request, pk):
        _require_faces()
        crop = (
            user_faces(request.user)
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
class PhotoFacesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Faces of a photo", responses={200: FaceSerializer(many=True)}
    )
    def get(self, request, file_uuid):
        _require_faces()
        faces = user_faces(request.user).filter(file_id=file_uuid).order_by("box_x")
        return Response(FaceSerializer(faces, many=True).data)


@extend_schema(tags=["Photos - Faces"])
class FaceStatusView(APIView):
    """Whether face grouping is on for the user, and how far it has come."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Face grouping status")
    def get(self, request):
        _require_faces()
        return Response(
            {"enabled": faces_enabled(request.user), **face_progress(request.user)}
        )
