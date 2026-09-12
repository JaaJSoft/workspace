from rest_framework import serializers

from .models import Tag


class TagSerializer(serializers.ModelSerializer):
    file_count = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = [
            "uuid",
            "name",
            "icon",
            "color",
            "is_favorite",
            "file_count",
            "created_at",
        ]
        read_only_fields = ["uuid", "created_at"]

    def get_file_count(self, obj):
        # Annotated by `tags_with_usage`; a tag returned from a write path
        # (create, assignment) has not been counted and reports zero.
        return getattr(obj, "file_count", 0)

    def validate_name(self, value):
        user = self.context["request"].user
        qs = Tag.objects.filter(owner=user, name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A tag with this name already exists.")
        return value

    def create(self, validated_data):
        validated_data["owner"] = self.context["request"].user
        return super().create(validated_data)
