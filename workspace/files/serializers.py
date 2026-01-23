from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from .models import File


class FileSerializer(serializers.ModelSerializer):
    path = serializers.SerializerMethodField()
    is_folder = serializers.SerializerMethodField()
    is_file = serializers.SerializerMethodField()

    class Meta:
        model = File
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

    @extend_schema_field(OpenApiTypes.STR)
    def get_path(self, obj):
        return obj.get_path()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_folder(self, obj):
        return obj.is_folder()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_file(self, obj):
        return obj.is_file()

    def validate(self, attrs):
        if self.instance is not None:
            errors = {}
            initial_data = self.initial_data or {}

            if 'uuid' in initial_data:
                incoming_uuid = str(initial_data.get('uuid')).lower()
                if incoming_uuid != str(self.instance.uuid).lower():
                    errors['uuid'] = 'This field is immutable.'

            if 'owner' in initial_data:
                incoming_owner = str(initial_data.get('owner'))
                if incoming_owner != str(self.instance.owner_id):
                    errors['owner'] = 'This field is immutable.'

            if 'node_type' in initial_data:
                if str(initial_data.get('node_type')) != self.instance.node_type:
                    errors['node_type'] = 'This field is immutable.'

            if errors:
                raise serializers.ValidationError(errors)

        return super().validate(attrs)

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)
