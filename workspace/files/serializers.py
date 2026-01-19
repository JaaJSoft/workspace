from rest_framework import serializers
from .models import FileNode


class FileNodeSerializer(serializers.ModelSerializer):
    path = serializers.SerializerMethodField()
    is_folder = serializers.SerializerMethodField()
    is_file = serializers.SerializerMethodField()

    class Meta:
        model = FileNode
        fields = [
            'uuid',
            'name',
            'node_type',
            'parent',
            'content',
            'size',
            'mime_type',
            'owner',
            'created_at',
            'updated_at',
            'path',
            'is_folder',
            'is_file',
        ]
        read_only_fields = ['owner', 'created_at', 'updated_at', 'size']

    def get_path(self, obj):
        return obj.get_path()

    def get_is_folder(self, obj):
        return obj.is_folder()

    def get_is_file(self, obj):
        return obj.is_file()

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)


class FileNodeTreeSerializer(serializers.ModelSerializer):
    """Serializer with children for tree representation."""
    children = serializers.SerializerMethodField()

    class Meta:
        model = FileNode
        fields = [
            'uuid',
            'name',
            'node_type',
            'parent',
            'size',
            'mime_type',
            'created_at',
            'updated_at',
            'children',
        ]

    def get_children(self, obj):
        if obj.is_folder():
            children = obj.children.all()
            return FileNodeTreeSerializer(children, many=True).data
        return []
