import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from workspace.common.serializers import (
    ApiModelSerializer,
    SummaryField,
    UuidListSerializer,
)
from workspace.common.services.mentions import render_comment_body
from workspace.users.serializers import UserSummarySerializer

from .models import (
    Epic,
    Label,
    Project,
    ProjectMember,
    ProjectNotificationLevel,
    Sprint,
    Subtask,
    Task,
    TaskAttachment,
    TaskComment,
    TaskStatus,
)
from .queries import project_users_qs
from .services.links import RELATIONS
from .services.references import KEY_RE

User = get_user_model()

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def save_written_columns(instance, validated_data, *, also=()):
    """Assign *validated_data* to *instance* and write only those columns.

    ``ModelSerializer.update`` ends in a bare ``save()``, which republishes
    every column as loaded when the request began - a state transition or a
    stamp another writer landed meanwhile would be reverted. *also* names
    columns the model maintains itself (``auto_now``).
    """
    for attr, value in validated_data.items():
        setattr(instance, attr, value)
    columns = [*validated_data, *also]
    if columns:
        instance.save(update_fields=columns)
    return instance


def _validate_hex_color(value):
    """Raise unless *value* is empty or a #rrggbb hex string.

    Shared by every serializer whose color is interpolated into an inline
    style, so no caller can slip through a CSS value (url(), a keyword)
    the picker never offers.
    """
    if value and not _HEX_COLOR.match(value):
        raise serializers.ValidationError(_("Color must be a #rrggbb value."))
    return value


def _of_the_project(queryset, context):
    return queryset.filter(project=context["project"])


def _assignable(queryset, context):
    return project_users_qs(context["project"])


def _open_sprints(queryset, context):
    # Closed sprints are history: tasks can only join open ones.
    return _of_the_project(queryset, context).exclude(state=Sprint.State.CLOSED)


def _anyone(queryset, context):
    """Every row may be named: who may write the field is the action's rule."""
    return queryset


class GroupSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class ProjectSerializer(ApiModelSerializer):
    # Without this DRF would require key on create; create() auto-generates it.
    key = serializers.CharField(required=False)
    groups = SummaryField(
        GroupSummarySerializer,
        queryset=Group.objects.all(),
        narrow=_anyone,
        many=True,
        required=False,
    )
    is_archived = serializers.BooleanField(required=False)
    done_retention_days = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=365
    )
    # allow_blank: "" is the "estimation disabled" state, not a missing value.
    estimate_unit = serializers.ChoiceField(
        choices=Project.EstimateUnit.choices, required=False, allow_blank=True
    )
    type = serializers.ChoiceField(choices=Project.Type.choices, required=False)
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
            "done_retention_days",
            "estimate_unit",
            "is_archived",
            "archived_at",
            "my_role",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["archived_at", "created_at", "updated_at"]

    def get_my_role(self, obj) -> str:
        # Set by the queryset annotation; group-only access has no
        # membership row and always means plain member.
        return getattr(obj, "_my_role", None) or ProjectMember.Role.MEMBER

    def validate_type(self, value):
        # Switching board models moves tasks and retires sprints, so it goes
        # through the convert endpoint rather than a field write.
        if self.instance is not None and value != self.instance.type:
            raise serializers.ValidationError(
                _("Use the convert endpoint to change the project type.")
            )
        if self.instance is None and value == Project.Type.PERSONAL:
            raise serializers.ValidationError(
                _("Personal projects are created automatically.")
            )
        return value

    def validate_key(self, value):
        value = value.strip().upper()
        if not KEY_RE.fullmatch(value):
            raise serializers.ValidationError(
                _("Use 2-10 letters and digits, starting with a letter.")
            )
        existing = Project.objects.filter(key=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                _("Another project already uses this key.")
            )
        return value

    def validate(self, attrs):
        if (
            self.instance is not None
            and self.instance.type == Project.Type.PERSONAL
            and attrs.get("groups")
        ):
            raise serializers.ValidationError(
                {"groups": _("Personal projects cannot be attached to groups.")}
            )
        return attrs

    def update(self, instance, validated_data):
        groups = validated_data.pop("groups", None)
        if "is_archived" in validated_data:
            archived = validated_data.pop("is_archived")
            if archived != instance.is_archived:
                validated_data["archived_at"] = timezone.now() if archived else None
        save_written_columns(instance, validated_data, also=["updated_at"])
        if groups is not None:
            instance.groups.set(groups)
        return instance


class MemberSerializer(ApiModelSerializer):
    user = SummaryField(
        UserSummarySerializer, queryset=User.objects.all(), narrow=_anyone
    )
    role = serializers.ChoiceField(
        choices=ProjectMember.Role.choices, default=ProjectMember.Role.MEMBER
    )

    class Meta:
        model = ProjectMember
        fields = ["uuid", "user", "role", "joined_at"]
        read_only_fields = ["joined_at"]


class ProjectUserSerializer(serializers.Serializer):
    """Someone a task can be assigned to: a member, or a user of a group the
    project is attached to (``queries.project_users``)."""

    id = serializers.CharField(source="pk", read_only=True)
    username = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)


class ProjectMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)


class ProjectNotificationLevelSerializer(serializers.Serializer):
    level = serializers.ChoiceField(choices=ProjectNotificationLevel.Level.choices)


class TaskWatchSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["watching", "muted"])


class LabelSerializer(ApiModelSerializer):
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
                _("A label with this name already exists in this project.")
            )
        return value

    def validate_color(self, value):
        return _validate_hex_color(value)


class EpicSerializer(ApiModelSerializer):
    is_closed = serializers.BooleanField(required=False)
    # Progress rollup, annotated by the viewset; absent on unannotated
    # instances (create/update responses return 0s there).
    task_count = serializers.IntegerField(read_only=True, default=0)
    done_task_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Epic
        fields = [
            "uuid",
            "name",
            "color",
            "description",
            "target_date",
            "is_closed",
            "task_count",
            "done_task_count",
        ]

    def validate_name(self, value):
        project = self.context["project"]
        existing = project.epics.filter(name=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                _("An epic with this name already exists in this project.")
            )
        return value

    def validate_color(self, value):
        return _validate_hex_color(value)


class SprintSerializer(ApiModelSerializer):
    # Progress rollup, annotated by the viewset; absent on unannotated
    # instances (create/update responses return 0s there).
    task_count = serializers.IntegerField(read_only=True, default=0)
    done_task_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Sprint
        fields = [
            "uuid",
            "name",
            "goal",
            "state",
            "start_date",
            "end_date",
            "closed_at",
            "task_count",
            "done_task_count",
            "created_at",
        ]
        read_only_fields = ["state", "closed_at", "created_at"]

    def validate_name(self, value):
        project = self.context["project"]
        existing = project.sprints.filter(name=value)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                _("A sprint with this name already exists in this project.")
            )
        return value

    def validate(self, attrs):
        start = attrs.get(
            "start_date", self.instance.start_date if self.instance else None
        )
        end = attrs.get("end_date", self.instance.end_date if self.instance else None)
        if start is not None and end is not None and end < start:
            raise serializers.ValidationError(
                {"end_date": _("End date cannot be before the start date.")}
            )
        return attrs

    def update(self, instance, validated_data):
        return save_written_columns(instance, validated_data)


class TaskStatusSerializer(ApiModelSerializer):
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
                _("A column with this name already exists in this project.")
            )
        return value

    def validate_category(self, value):
        if self.instance is not None and value != self.instance.category:
            raise serializers.ValidationError(_("Category cannot be changed."))
        return value

    def validate_color(self, value):
        return _validate_hex_color(value)


class ProjectSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    key = serializers.CharField()


class TaskStatusSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    category = serializers.ChoiceField(choices=TaskStatus.Category.choices)
    color = serializers.CharField(allow_blank=True)


class LabelSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.CharField(allow_blank=True)


class EpicSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.CharField(allow_blank=True)


class SprintSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    state = serializers.ChoiceField(choices=Sprint.State.choices)


class TaskSerializer(ApiModelSerializer):
    project = ProjectSummarySerializer(read_only=True)
    status = SummaryField(
        TaskStatusSummarySerializer,
        queryset=TaskStatus.objects.all(),
        narrow=_of_the_project,
        required=False,
    )
    # Anyone the project does not reach is refused with the message an
    # unknown id gets, so the field names nobody.
    assignees = SummaryField(
        UserSummarySerializer,
        queryset=User.objects.all(),
        narrow=_assignable,
        many=True,
        required=False,
    )
    labels = SummaryField(
        LabelSummarySerializer,
        queryset=Label.objects.all(),
        narrow=_of_the_project,
        many=True,
        required=False,
    )
    epic = SummaryField(
        EpicSummarySerializer,
        queryset=Epic.objects.all(),
        narrow=_of_the_project,
        required=False,
        allow_null=True,
    )
    sprint = SummaryField(
        SprintSummarySerializer,
        queryset=Sprint.objects.all(),
        narrow=_open_sprints,
        required=False,
        allow_null=True,
    )
    created_by = UserSummarySerializer(read_only=True, allow_null=True)
    reference = serializers.CharField(read_only=True)
    estimate = serializers.DecimalField(
        max_digits=6, decimal_places=1, required=False, allow_null=True, min_value=0
    )

    class Meta:
        model = Task
        fields = [
            "uuid",
            "project",
            "title",
            "description",
            "status",
            "priority",
            "due_date",
            "start_date",
            "estimate",
            "assignees",
            "labels",
            "epic",
            "sprint",
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

    def validate(self, attrs):
        start = attrs.get(
            "start_date", self.instance.start_date if self.instance else None
        )
        due = attrs.get("due_date", self.instance.due_date if self.instance else None)
        if start is not None and due is not None and start > due:
            raise serializers.ValidationError(
                {"start_date": _("Start date cannot be after the due date.")}
            )
        return attrs

    def update(self, instance, validated_data):
        assignees = validated_data.pop("assignees", None)
        labels = validated_data.pop("labels", None)
        save_written_columns(instance, validated_data, also=["updated_at"])
        if assignees is not None:
            instance.assignees.set(assignees)
        if labels is not None:
            instance.labels.set(labels)
        return instance


class TaskCalendarSerializer(serializers.ModelSerializer):
    """Read-only projection of a task for the calendar overlay.

    Deliberately thinner than ``TaskSerializer``: the calendar paints a
    label and links out to the board, so assignees, labels and description
    would only cost joins nobody renders.
    """

    reference = serializers.SerializerMethodField()
    project_uuid = serializers.UUIDField(source="project.uuid", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    url = serializers.SerializerMethodField()
    card_url = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "uuid",
            "title",
            "due_date",
            "priority",
            "reference",
            "project_uuid",
            "project_name",
            "url",
            "card_url",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_reference(self, obj):
        return f"{obj.project.key}-{obj.number}"

    @extend_schema_field(OpenApiTypes.STR)
    def get_url(self, obj):
        return obj.get_absolute_url()

    @extend_schema_field(OpenApiTypes.STR)
    def get_card_url(self, obj):
        return reverse("projects_ui:task_card", args=[obj.project_id, obj.uuid])


class _InProject(serializers.PrimaryKeyRelatedField):
    """A row of the project at hand, named by its identifier."""

    def get_queryset(self):
        return _of_the_project(super().get_queryset(), self.context)


class _Assignable(serializers.PrimaryKeyRelatedField):
    """A user the project at hand reaches; anyone else answers like an
    unknown id."""

    def get_queryset(self):
        return _assignable(super().get_queryset(), self.context)


class _Unassignable(serializers.PrimaryKeyRelatedField):
    """A user who can hold a task of the project at hand: someone it reaches,
    or someone who left it and is still assigned. Anyone else answers like an
    unknown id."""

    def get_queryset(self):
        project = self.context["project"]
        return (
            super()
            .get_queryset()
            .filter(
                Q(pk__in=project_users_qs(project).values("pk"))
                | Q(assigned_tasks__project=project)
            )
            .distinct()
        )


class TaskReorderSerializer(UuidListSerializer):
    """The tasks of one column, in the order they should take."""

    status = _InProject(queryset=TaskStatus.objects.all())


class TaskBatchMoveSerializer(serializers.Serializer):
    status = _InProject(queryset=TaskStatus.objects.all())


class TaskBatchSprintSerializer(serializers.Serializer):
    """A null sprint returns the tasks to the pool."""

    sprint = _InProject(queryset=Sprint.objects.all(), allow_null=True)


class TaskBatchEditSerializer(serializers.Serializer):
    """The changes a lot applies to every task it names: at least one.

    Assignees and labels are add/remove sets rather than replacement lists:
    the selection's tasks carry different values, and "put Alice on these"
    must not strip whoever else was on them.
    """

    assign = _Assignable(queryset=User.objects.all(), many=True, required=False)
    unassign = _Unassignable(queryset=User.objects.all(), many=True, required=False)
    add_labels = _InProject(queryset=Label.objects.all(), many=True, required=False)
    remove_labels = _InProject(queryset=Label.objects.all(), many=True, required=False)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, required=False)
    due_date = serializers.DateField(required=False, allow_null=True)

    OPERATIONS = (
        "assign",
        "unassign",
        "add_labels",
        "remove_labels",
        "priority",
        "due_date",
    )

    def validate(self, attrs):
        if not any(field in attrs for field in self.OPERATIONS):
            raise serializers.ValidationError(_("No change requested."))
        # A repeated id would insert the same relation row twice and trip
        # the through table's unique constraint into a 500.
        for field in ("assign", "unassign", "add_labels", "remove_labels"):
            pks = [obj.pk for obj in attrs.get(field, ())]
            if len(set(pks)) != len(pks):
                raise serializers.ValidationError({field: f"Duplicate ids in {field}."})
        if {u.pk for u in attrs.get("assign", ())} & {
            u.pk for u in attrs.get("unassign", ())
        }:
            raise serializers.ValidationError(
                {"unassign": _("A user cannot be both assigned and unassigned.")}
            )
        if {lbl.pk for lbl in attrs.get("add_labels", ())} & {
            lbl.pk for lbl in attrs.get("remove_labels", ())
        }:
            raise serializers.ValidationError(
                {"remove_labels": _("A label cannot be both added and removed.")}
            )
        return attrs


class ProjectConvertSerializer(serializers.Serializer):
    """Target board model for a project conversion."""

    type = serializers.ChoiceField(
        choices=[Project.Type.KANBAN, Project.Type.SCRUM],
    )


class SprintCompleteSerializer(serializers.Serializer):
    """Completion payload; a null/absent move_to sends tasks to the backlog."""

    move_to = _InProject(
        queryset=Sprint.objects.all(), required=False, allow_null=True, default=None
    )


class StatusDeleteQuerySerializer(serializers.Serializer):
    """``?move_to=``: the column that takes the tasks of the one deleted."""

    move_to = _InProject(
        queryset=TaskStatus.objects.all(), required=False, allow_null=True, default=None
    )


class SubtaskSerializer(ApiModelSerializer):
    is_done = serializers.BooleanField(source="done", required=False)

    class Meta:
        model = Subtask
        fields = ["uuid", "title", "is_done", "position", "created_at"]
        read_only_fields = ["position", "created_at"]

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("Title cannot be blank."))
        return value


class TaskLinkCreateSerializer(serializers.Serializer):
    target = serializers.UUIDField()
    relation = serializers.ChoiceField(choices=sorted(RELATIONS))


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
    body_html = serializers.SerializerMethodField()

    class Meta:
        model = TaskComment
        fields = [
            "uuid",
            "task",
            "author",
            "body",
            "body_html",
            "edited_at",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_body_html(self, obj):
        return render_comment_body(obj.body, self.context.get("mention_map") or {})


class TaskCommentBodySerializer(serializers.Serializer):
    body = serializers.CharField()


class TaskAttachmentSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="original_name", read_only=True)
    added_by = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskAttachment
        fields = [
            "uuid",
            "name",
            "size",
            "mime_type",
            "type",
            "category",
            "download_url",
            "added_by",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_added_by(self, obj):
        return obj.added_by.username if obj.added_by else None

    @extend_schema_field(OpenApiTypes.STR)
    def get_download_url(self, obj):
        return reverse(
            "project-task-attachment-download",
            kwargs={
                "project_uuid": obj.task.project_id,
                "task_uuid": obj.task_id,
                "uuid": obj.uuid,
            },
        )


class TaskAttachmentCreateSerializer(serializers.Serializer):
    file_uuids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class TaskFileLinkCreateSerializer(serializers.Serializer):
    file_uuids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=50
    )
    # Permission of the project share created for a file not yet shared
    # with the project; an existing share keeps its own.
    permission = serializers.ChoiceField(
        choices=[("ro", "Read only"), ("rw", "Read & write")], default="ro"
    )
