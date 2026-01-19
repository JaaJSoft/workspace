from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from .models import FileNode
from .serializers import FileNodeSerializer, FileNodeTreeSerializer


class FileNodeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing files and folders in a tree structure.

    list: Get all files/folders
    retrieve: Get a specific file/folder
    create: Create a new file/folder
    update: Update a file/folder
    destroy: Delete a file/folder (cascades to children)
    tree: Get tree representation starting from a node
    roots: Get all root nodes (nodes without parent)
    """
    queryset = FileNode.objects.all()
    serializer_class = FileNodeSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['node_type', 'parent', 'owner']
    search_fields = ['name', 'mime_type']
    ordering_fields = ['name', 'created_at', 'updated_at', 'size']
    ordering = ['node_type', 'name']

    def get_queryset(self):
        """Filter by current user's files."""
        return FileNode.objects.filter(owner=self.request.user)

    @action(detail=True, methods=['get'])
    def tree(self, request, pk=None):
        """Get tree representation starting from this node."""
        node = self.get_object()
        serializer = FileNodeTreeSerializer(node)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def roots(self, request):
        """Get all root nodes (nodes without parent)."""
        roots = self.get_queryset().filter(parent__isnull=True)
        serializer = FileNodeTreeSerializer(roots, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def children(self, request, pk=None):
        """Get direct children of a node."""
        node = self.get_object()
        if not node.is_folder():
            return Response(
                {'detail': 'This node is not a folder.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        children = node.children.all()
        serializer = self.get_serializer(children, many=True)
        return Response(serializer.data)
