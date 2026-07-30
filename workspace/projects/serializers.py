from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Label, Project, ProjectMember, Task, TaskComment, TaskStatus
from .queries import get_project_role
from .services.references import KEY_RE

User = get_user_model()


class ProjectSerializer(serializers.ModelSerializer):
    # Without this DRF would require key on create; create() auto-generates it.
    key = serializers.CharField(required=False)
    groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True, required=False
    )
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "uuid",
            "name",
            "key",
            "description",
            "type",
            "groups",
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

    def validate_key(self, value):
        value = value.strip().upper()
        if not KEY_RE.fullmatch(value):
            raise serializers.ValidationError(
                "Use 2-10 letters and digits, starting with a letter."
            )
        existing = Project.objects.filter(key=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("Another project already uses this key.")
        return value

    def validate_groups(self, groups):
        # Only newly added groups require the requester's membership:
        # a group attached by another admin must survive a list update
        # (and be removable) without locking the two admins out.
        attached = (
            set(self.instance.groups.values_list("pk", flat=True))
            if self.instance is not None
            else set()
        )
        added = {group.pk for group in groups} - attached
        if added:
            mine = set(
                self.context["request"]
                .user.groups.filter(pk__in=added)
                .values_list("pk", flat=True)
            )
            if added - mine:
                raise serializers.ValidationError(
                    "You can only attach groups you belong to."
                )
        return groups

    def validate(self, attrs):
        if (
            self.instance is not None
            and self.instance.type == Project.Type.PERSONAL
            and attrs.get("groups")
        ):
            raise serializers.ValidationError(
                {"groups": "Personal projects cannot be attached to groups."}
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
        read_only_fields = ["position"]

    def validate_name(self, value):
        project = self.context["project"]
        existing = project.statuses.filter(name=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                "A column with this name already exists in this project."
            )
        return value

    def validate_category(self, value):
        if self.instance is not None and value != self.instance.category:
            raise serializers.ValidationError("Category cannot be changed.")
        return value


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
    reference = serializers.SerializerMethodField()

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
            "number",
            "reference",
            "completed_at",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "position",
            "number",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    def get_reference(self, obj) -> str:
        # Context project avoids an N+1; the fallback costs a query per task.
        project = self.context.get("project") or obj.project
        return f"{project.key}-{obj.number}"

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


def _parse_uuid_list(value, field_name):
    """Manual UUID parsing instead of ListField(child=UUIDField): the orjson
    renderer used project-wide cannot serialize the int-keyed error dicts
    that per-item child validation produces (PinnedReorderSerializer
    precedent)."""
    import uuid as uuid_module

    parsed = []
    for item in value:
        if not isinstance(item, str):
            raise serializers.ValidationError(
                f"{field_name} items must be UUID strings."
            )
        try:
            parsed.append(uuid_module.UUID(item))
        except ValueError:
            raise serializers.ValidationError(f"Invalid UUID: {item}") from None
    if len(set(parsed)) != len(parsed):
        raise serializers.ValidationError(f"Duplicate UUIDs in {field_name}.")
    return parsed


class TaskReorderSerializer(serializers.Serializer):
    status = serializers.UUIDField()
    order = serializers.ListField()

    def validate_order(self, value):
        return _parse_uuid_list(value, "order")


class TaskMoveSerializer(serializers.Serializer):
    status = serializers.UUIDField()
    # max_length caps the IN clause of the bulk move: past a few thousand
    # parameters SQLite (a production target) errors out with "too many
    # SQL variables" and the request would 500 instead of 400.
    tasks = serializers.ListField(allow_empty=False, max_length=1000)

    def validate_tasks(self, value):
        return _parse_uuid_list(value, "tasks")


class StatusReorderSerializer(serializers.Serializer):
    order = serializers.ListField()

    def validate_order(self, value):
        return _parse_uuid_list(value, "order")


class TaskCommentAuthorSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="pk")
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        return f"/api/v1/users/{obj.pk}/avatar"


class TaskCommentSerializer(serializers.ModelSerializer):
    author = TaskCommentAuthorSerializer(read_only=True)

    class Meta:
        model = TaskComment
        fields = ["uuid", "task", "author", "body", "edited_at", "created_at"]
        read_only_fields = fields


class TaskCommentBodySerializer(serializers.Serializer):
    body = serializers.CharField()
