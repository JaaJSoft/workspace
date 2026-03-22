from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from .models import Tag
from .serializers_tags import TagSerializer


@extend_schema_view(
    list=extend_schema(summary="List tags", tags=['Tags']),
    create=extend_schema(summary="Create a tag", tags=['Tags']),
    partial_update=extend_schema(summary="Update a tag", tags=['Tags']),
    destroy=extend_schema(summary="Delete a tag", tags=['Tags']),
)
@extend_schema(tags=['Tags'])
class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    lookup_field = 'uuid'
    http_method_names = ['get', 'post', 'patch', 'delete']
    pagination_class = None

    def get_queryset(self):
        return Tag.objects.filter(owner=self.request.user)


from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from .models import File, FileTag
from workspace.files.services import FileService


class FileTagView(APIView):
    """Add or remove tags on a file."""

    @extend_schema(summary="Add a tag to a file", tags=['Tags'])
    def post(self, request, file_uuid):
        file_obj = get_object_or_404(
            FileService.user_files_qs(request.user), uuid=file_uuid,
        )
        tag_uuid = request.data.get('tag')
        if not tag_uuid:
            return Response({'tag': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)

        tag = Tag.objects.filter(uuid=tag_uuid, owner=request.user).first()
        if not tag:
            return Response({'tag': 'Invalid tag.'}, status=status.HTTP_400_BAD_REQUEST)

        if FileTag.objects.filter(file=file_obj, tag=tag).exists():
            return Response(
                {'detail': 'Tag already assigned.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        FileTag.objects.create(file=file_obj, tag=tag)
        return Response(TagSerializer(tag).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Remove a tag from a file", tags=['Tags'])
    def delete(self, request, file_uuid, tag_uuid):
        file_obj = get_object_or_404(
            FileService.user_files_qs(request.user), uuid=file_uuid,
        )
        ft = FileTag.objects.filter(file=file_obj, tag__uuid=tag_uuid).first()
        if not ft:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ft.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
