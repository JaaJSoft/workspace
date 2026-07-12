from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import serializers

from .models import Label, Project, ProjectMember, Task, TaskStatus
from .queries import get_project_role

User = get_user_model()


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

    def get_my_role(self, obj) -> str:
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


class MemberSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = ProjectMember
        fields = ["uuid", "user", "username", "role", "joined_at"]


class MemberWriteSerializer(serializers.Serializer):
    user = serializers.IntegerField()
    role = serializers.ChoiceField(
        choices=ProjectMember.Role.choices, default=ProjectMember.Role.MEMBER
    )


class MemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)


class LabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Label
        fields = ["uuid", "name", "color"]

    def validate_name(self, value):
        project = self.context["project"]
        existing = project.labels.filter(name=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                "A label with this name already exists in this project."
            )
        return value


class TaskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStatus
        fields = ["uuid", "name", "category", "color", "position"]


class TaskSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=TaskStatus.objects.none(), required=False
    )
    status_category = serializers.CharField(source="status.category", read_only=True)
    assignees = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, required=False
    )
    labels = serializers.PrimaryKeyRelatedField(
        queryset=Label.objects.none(), many=True, required=False
    )
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Task
        fields = [
            "uuid",
            "title",
            "description",
            "status",
            "status_category",
            "priority",
            "due_date",
            "assignees",
            "labels",
            "position",
            "completed_at",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "position",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["status"].queryset = project.statuses.all()
            self.fields["labels"].child_relation.queryset = project.labels.all()

    def validate_assignees(self, users):
        project = self.context["project"]
        for user in users:
            if get_project_role(user, project) is None:
                raise serializers.ValidationError(
                    f"{user.username} is not a member of this project."
                )
        return users


class TaskReorderSerializer(serializers.Serializer):
    """Validate the reorder payload.

    Manual UUID parsing in validate_order instead of
    ListField(child=UUIDField): the orjson renderer used project-wide
    cannot serialize the int-keyed error dicts that per-item child
    validation produces (PinnedReorderSerializer precedent).
    """

    status = serializers.UUIDField()
    order = serializers.ListField()

    def validate_order(self, value):
        import uuid as uuid_module

        parsed = []
        for item in value:
            if not isinstance(item, str):
                raise serializers.ValidationError("order items must be UUID strings.")
            try:
                parsed.append(uuid_module.UUID(item))
            except ValueError:
                raise serializers.ValidationError(f"Invalid UUID: {item}") from None
        if len(set(parsed)) != len(parsed):
            raise serializers.ValidationError("Duplicate UUIDs in order.")
        return parsed
