from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none
from workspace.files.services import FileService
from workspace.files.services.tags import (
    TagMergeError,
    merge_tags,
    purge_unused_tags,
    tags_with_usage,
)

from ..models import FileTag, Tag
from ..serializers_tags import TagSerializer


@extend_schema_view(
    list=extend_schema(summary="List tags", tags=["Files - Tags"]),
    create=extend_schema(summary="Create a tag", tags=["Files - Tags"]),
    partial_update=extend_schema(summary="Update a tag", tags=["Files - Tags"]),
    destroy=extend_schema(summary="Delete a tag", tags=["Files - Tags"]),
)
@extend_schema(tags=["Files - Tags"])
class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    lookup_field = "uuid"
    http_method_names = ["get", "post", "patch", "delete"]
    pagination_class = None

    def get_queryset(self):
        return tags_with_usage(self.request.user)

    @extend_schema(
        summary="Merge a tag into another one",
        description="Moves every assignment onto the target tag and deletes "
        "this one. A file carrying both keeps a single assignment.",
        request={
            "application/json": {
                "type": "object",
                "properties": {"into": {"type": "string", "format": "uuid"}},
            }
        },
        responses={200: TagSerializer},
    )
    @action(detail=True, methods=["post"])
    def merge(self, request, uuid=None):
        source = self.get_object()
        target_uuid = parse_uuid_or_none(request.data.get("into"))
        if target_uuid is None:
            return Response(
                {"into": "A target tag is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target = get_object_or_404(self.get_queryset(), uuid=target_uuid)
        try:
            merge_tags(source, target)
        except TagMergeError:
            return Response(
                {"into": "A tag cannot be merged into itself."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target = self.get_queryset().get(pk=target.pk)
        return Response(self.get_serializer(target).data)

    @extend_schema(
        summary="Delete every tag with no file",
        responses={
            200: {"type": "object", "properties": {"deleted": {"type": "integer"}}}
        },
    )
    @action(detail=False, methods=["post"], url_path="purge-unused")
    def purge_unused(self, request):
        return Response({"deleted": purge_unused_tags(request.user)})


class FileTagView(APIView):
    """Add or remove tags on a file."""

    @extend_schema(summary="Add a tag to a file", tags=["Files - Tags"])
    def post(self, request, file_uuid):
        file_obj = get_object_or_404(
            FileService.user_files_qs(request.user),
            uuid=file_uuid,
        )
        tag_uuid_raw = request.data.get("tag")
        if not tag_uuid_raw:
            return Response(
                {"tag": "This field is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        tag_uuid = parse_uuid_or_none(tag_uuid_raw)
        if tag_uuid is None:
            return Response({"tag": "Invalid tag."}, status=status.HTTP_400_BAD_REQUEST)

        tag = Tag.objects.filter(uuid=tag_uuid, owner=request.user).first()
        if not tag:
            return Response({"tag": "Invalid tag."}, status=status.HTTP_400_BAD_REQUEST)

        if FileTag.objects.filter(file=file_obj, tag=tag).exists():
            return Response(
                {"detail": "Tag already assigned."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        FileTag.objects.create(file=file_obj, tag=tag)
        return Response(TagSerializer(tag).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Remove a tag from a file", tags=["Files - Tags"])
    def delete(self, request, file_uuid, tag_uuid):
        file_obj = get_object_or_404(
            FileService.user_files_qs(request.user),
            uuid=file_uuid,
        )
        ft = FileTag.objects.filter(file=file_obj, tag__uuid=tag_uuid).first()
        if not ft:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ft.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
