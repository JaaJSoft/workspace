from django.contrib.auth.models import Group
from rest_framework import serializers

from .models import Project, ProjectMember


class ProjectSerializer(serializers.ModelSerializer):
    group = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), allow_null=True, required=False
    )
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "uuid",
            "name",
            "description",
            "type",
            "group",
            "archived_at",
            "my_role",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["type", "archived_at", "created_at", "updated_at"]

    def get_my_role(self, obj):
        # Set by the queryset annotation; group-only access has no
        # membership row and always means plain member.
        return getattr(obj, "_my_role", None) or ProjectMember.Role.MEMBER

    def validate_group(self, group):
        if group is None:
            return group
        user = self.context["request"].user
        if not user.groups.filter(pk=group.pk).exists():
            raise serializers.ValidationError(
                "You can only attach a group you belong to."
            )
        return group

    def validate(self, attrs):
        if (
            self.instance is not None
            and self.instance.type == Project.Type.PERSONAL
            and attrs.get("group") is not None
        ):
            raise serializers.ValidationError(
                {"group": "Personal projects cannot be attached to a group."}
            )
        return attrs
