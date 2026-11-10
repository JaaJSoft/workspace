from contextlib import contextmanager

from django.db.models.manager import BaseManager
from django.utils.translation import gettext, gettext_lazy
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied

from workspace.common.services.mentions import render_comment_body
from workspace.files.queries import (
    FilePermission,
    accessible_files_q,
    annotate_for_serializer,
    can_create_in,
    get_permission,
    reads_full_path,
    visible_path,
)
from workspace.files.services import FileService
from workspace.files.services._names import (
    FILE_NAME_TAKEN,
    FOLDER_NAME_TAKEN,
    NameTaken,
)
from workspace.files.services.files import GroupMismatch
from workspace.files.services.image_edits import (
    ROTATIONS,
    edit_problem,
    normalize_edit,
)
from workspace.files.services.locking import (
    LockConflict,
    StaleContent,
    conflicting_lock,
)
from workspace.files.services.operations import (
    MAX_ITEMS,
    TARGETED_KINDS,
    TRANSFER_KINDS,
)
from workspace.files.services.refusals import Refusal, Refused
from workspace.files.services.scanning.policy import QuarantinedSource
from workspace.files.services.shortcuts import (
    UNAVAILABLE,
    TargetUnreachable,
    attach_resolutions,
    create_shortcut,
    resolution_of,
)
from workspace.files.services.transfers import OnConflict, move_item
from workspace.files.services.uploads import CHUNK_SIZE

from .models import File, FileComment, FileOperation, UploadSession, canonical_name


class FileLocked(APIException):
    status_code = 423
    default_detail = gettext_lazy("File is locked by another user.")
    default_code = "locked"


class FileContentStale(APIException):
    status_code = 412
    default_detail = gettext_lazy("The file changed since it was loaded.")
    default_code = "precondition_failed"


# The columns a PATCH may write through the serializer. The primary key is out:
# a request may legitimately echo it, and ``update_fields`` rejects it.
_WRITABLE_COLUMNS = frozenset(
    field.name for field in File._meta.concrete_fields if not field.primary_key
)


def locked_payload(holder):
    """The 423 body, holder included so the UI can name who is editing."""
    body = {"detail": FileLocked.default_detail}
    if holder is not None:
        body["locked_by"] = {"id": holder.pk, "username": holder.username}
    return body


def ensure_unlocked(user, target):
    """Raise 423 unless *user* may write *target* right now."""
    holder = conflicting_lock(target, user)
    if holder is not None:
        raise FileLocked(locked_payload(holder))


def name_taken_error(error):
    """The 400 a taken name owes the client, built from constants only."""
    message = FOLDER_NAME_TAKEN if error.held_by_folder else FILE_NAME_TAKEN
    return serializers.ValidationError({"name": [message]})


@contextmanager
def name_refusals():
    """A name taken after validation passed answers like one taken before."""
    try:
        yield
    except NameTaken as error:
        raise name_taken_error(error) from error


def _rename(user, node, name):
    """Rename *node* the way every other path does: a move within its folder."""
    try:
        move_item(user, node, node.parent, name=name)
    except Refused as refusal:
        if refusal.reason == Refusal.NAME_TAKEN:
            held_by_folder = getattr(refusal.__cause__, "held_by_folder", False)
            message = FOLDER_NAME_TAKEN if held_by_folder else FILE_NAME_TAKEN
            raise serializers.ValidationError({"name": [message]}) from refusal
        if refusal.reason == Refusal.LOCKED:
            raise FileLocked(locked_payload(refusal.holder)) from refusal
        raise PermissionDenied(
            gettext("Renaming this file is not allowed.")
        ) from refusal


@contextmanager
def content_write_refusals():
    """Turn the service's refusals into the answers they owe the client.

    A content write is decided inside the write itself, so both refusals reach
    the caller as exceptions rather than as a return value - and every endpoint
    that writes content owes them the same two status codes.
    """
    try:
        yield
    except LockConflict as exc:
        raise FileLocked(locked_payload(exc.holder)) from exc
    except StaleContent as exc:
        raise FileContentStale(
            {
                "detail": FileContentStale.default_detail,
                "content_hash": exc.current_hash,
            }
        ) from exc
    except QuarantinedSource as exc:
        raise PermissionDenied(gettext("File is quarantined.")) from exc


def ensure_replaceable(user, target):
    """Raise unless *user* may overwrite *target*'s content right now."""
    permission = get_permission(user, target)
    if permission is None or permission < FilePermission.WRITE:
        raise PermissionDenied(gettext("You cannot replace the existing file."))
    ensure_unlocked(user, target)


def validate_node_name(value):
    """Refuse the path separators a name would be split on downstream.

    Returns *value* composed, the form node names are stored in.
    """
    if "/" in value:
        raise serializers.ValidationError(
            gettext("File and folder names must not contain '/'.")
        )
    if "\\" in value:
        raise serializers.ValidationError(
            gettext("File and folder names must not contain a backslash.")
        )
    if value in (".", ".."):
        raise serializers.ValidationError(
            gettext("File and folder names must not be '.' or '..'.")
        )
    return canonical_name(value)


def _require_annotation(obj, name):
    """Fetch an annotation from *obj* or raise a loud error.

    ``FileSerializer`` deliberately has no DB fallback when an annotation is
    missing — callers must pipe their queryset through
    ``annotate_for_serializer`` before handing it to the serializer.
    This helper turns a missing annotation into a clear exception instead of a
    silent N+1.
    """
    try:
        return getattr(obj, name)
    except AttributeError as exc:
        raise AttributeError(
            f"FileSerializer requires the {name!r} annotation. "
            f"Use annotate_for_serializer(qs, user) on the queryset."
        ) from exc


def _node_icon(node):
    from workspace.files.services.filetype import get_icon

    if node.node_type == File.NodeType.FOLDER:
        return node.icon or "folder"
    return get_icon(node.type or "")


def _node_color(node):
    from workspace.files.services.filetype import get_color

    if node.node_type == File.NodeType.FOLDER:
        return node.color or "text-warning"
    return get_color(node.type or "")


# What a shortcut shows when its viewer cannot reach the target.
UNREACHABLE_SHORTCUT_ICON = "link-2-off"


def _viewer(serializer):
    request = serializer.context.get("request")
    return getattr(request, "user", None)


class ReachableNodeField(serializers.PrimaryKeyRelatedField):
    """A live node the requesting user can reach.

    Any other uuid fails as a missing one, before any rule looks at the node:
    whatever answer a rule gave on a node out of reach would tell the caller
    that it exists.
    """

    def get_queryset(self):
        user = _viewer(self)
        if user is None:
            return File.objects.none()
        # distinct: the share branches of the access filter join one row per
        # share, and get() would find a node shared twice two times.
        return File.objects.filter(
            accessible_files_q(user), deleted_at__isnull=True
        ).distinct()


class FileListSerializer(serializers.ListSerializer):
    """Resolves every shortcut of the page in one batch before serializing.

    Without it each shortcut would resolve its target on its own: two or
    three queries per row.
    """

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, BaseManager) else data
        items = list(iterable)
        user = _viewer(self)
        if user is not None:
            attach_resolutions(user, items)
        return [self.child.to_representation(item) for item in items]


class FileSerializer(serializers.ModelSerializer):
    is_folder = serializers.SerializerMethodField(
        help_text="True when node_type is 'folder'."
    )
    is_file = serializers.SerializerMethodField(
        help_text="True when node_type is 'file'."
    )
    is_favorite = serializers.SerializerMethodField(
        help_text="True when the current user has favorited this item."
    )
    is_viewable = serializers.SerializerMethodField(
        help_text="True if file can be viewed in browser."
    )
    content_url = serializers.SerializerMethodField(
        help_text="URL to fetch file content for viewing."
    )
    is_pinned = serializers.SerializerMethodField(
        help_text="True when the current user has pinned this folder to the sidebar."
    )
    is_shared = serializers.SerializerMethodField(
        help_text="True when the file has been shared with at least one user."
    )
    tags = serializers.SerializerMethodField(help_text="Tags assigned to this file.")
    path = serializers.SerializerMethodField(
        help_text=(
            "Slash-separated path from the root of the owner's tree or the "
            "group folder. A node that only reached the user through a share "
            "carries its name alone: the folders above it are not theirs."
        )
    )
    has_children = serializers.SerializerMethodField(
        help_text="True when a folder contains child folders (folders only)."
    )
    scan_status = serializers.SerializerMethodField(
        help_text="Malware scan verdict: clean, infected, skipped, error, or empty when not scanned."
    )
    scan_signature = serializers.SerializerMethodField(
        help_text="Detected malware signature name, when the verdict is 'infected'."
    )
    is_quarantined = serializers.SerializerMethodField(
        help_text="True when the malware policy currently blocks this file."
    )
    processing_status = serializers.SerializerMethodField(
        help_text=(
            "Where the upload pipeline (malware scan, thumbnail, metadata) stands "
            "on the file's current content: pending, processing, ready, or "
            "quarantined when the malware policy blocks it."
        )
    )
    type_icon = serializers.SerializerMethodField(
        help_text="Lucide icon for the entry: the folder's own icon, or the one the file type registry resolves."
    )
    type_color = serializers.SerializerMethodField(
        help_text="Tailwind text color class that goes with type_icon."
    )
    shortcut_target = ReachableNodeField(
        required=False,
        write_only=True,
        help_text=(
            "For node_type 'shortcut', the file or folder it opens: one the "
            "caller can open. Set on creation only. A shortcut to a shortcut "
            "points at that shortcut's target."
        ),
    )
    shortcut = serializers.SerializerMethodField(
        help_text=(
            "Null unless node_type is 'shortcut'. Otherwise 'state' is 'ok' "
            "when the caller can open the target, 'trashed' when it sits in a "
            "trash they can restore it from, 'unavailable' otherwise, and "
            "'target' describes the target in the first two states only. "
            "A shortcut has no content: open the target by its own uuid."
        )
    )
    on_conflict = serializers.ChoiceField(
        choices=["error", "rename", "replace"],
        default="error",
        write_only=True,
        required=False,
        help_text=(
            "What to do when a file with the same name already exists in the "
            "target folder, on creation: 'error' rejects the request "
            "(default), 'rename' stores the file under a free 'name "
            "(Copy).ext' variant, 'replace' writes the content into the "
            "existing file and answers 200 with it."
        ),
    )

    class Meta:
        model = File
        fields = [
            "uuid",
            "name",
            "node_type",
            "parent",
            "content",
            "size",
            "mime_type",
            "type",
            "content_hash",
            "icon",
            "color",
            "owner",
            "group",
            "created_at",
            "updated_at",
            "deleted_at",
            "path",
            "is_folder",
            "is_file",
            "is_favorite",
            "category",
            "is_viewable",
            "content_url",
            "is_pinned",
            "is_shared",
            "tags",
            "has_children",
            "on_conflict",
            "scan_status",
            "scan_signature",
            "is_quarantined",
            "processing_status",
            "type_icon",
            "type_color",
            "has_thumbnail",
            "image_edit",
            "shortcut_target",
            "shortcut",
        ]
        list_serializer_class = FileListSerializer
        read_only_fields = [
            "has_thumbnail",
            "image_edit",
            "owner",
            "created_at",
            "updated_at",
            "deleted_at",
            "size",
            "content_hash",
            "path",
            "category",
            "is_favorite",
            "is_pinned",
            "is_shared",
            "tags",
            "scan_status",
            "scan_signature",
            "is_quarantined",
            "processing_status",
        ]
        extra_kwargs = {
            "uuid": {
                "help_text": "Unique identifier. Generated by the server if omitted."
            },
            "name": {"help_text": "Display name of the file or folder."},
            "node_type": {"help_text": "One of 'file', 'folder' or 'shortcut'."},
            "parent": {"help_text": "Parent folder UUID."},
            "content": {
                "help_text": "File payload. Must be empty for folders.",
                "allow_empty_file": True,
                "required": False,
                "write_only": True,
            },
            "size": {"help_text": "File size in bytes."},
            "content_hash": {
                "help_text": "SHA-256 hex digest of the content. Empty for folders."
            },
            "mime_type": {"help_text": "MIME type for file nodes."},
            "owner": {"help_text": "Owner user id."},
            "created_at": {"help_text": "Creation timestamp."},
            "updated_at": {"help_text": "Last update timestamp."},
        }

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_folder(self, obj):
        return obj.is_folder()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_file(self, obj):
        return obj.is_file()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_favorite(self, obj):
        return bool(_require_annotation(obj, "is_favorite"))

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_viewable(self, obj):
        from workspace.files.services.scanning.policy import is_blocked

        if is_blocked(obj):
            return False
        # Delegate to the model so the MIME fallback (for 'unknown'/empty type)
        # and the non-FILE guard stay in one place. File.is_viewable() already
        # returns False for folders.
        return obj.is_viewable()

    def _resolution(self, obj):
        user = _viewer(self)
        if user is None:
            return UNAVAILABLE
        return resolution_of(user, obj)

    @extend_schema_field(OpenApiTypes.STR)
    def get_type_icon(self, obj):
        if obj.node_type == File.NodeType.SHORTCUT:
            target = self._resolution(obj).target
            return _node_icon(target) if target else UNREACHABLE_SHORTCUT_ICON
        return _node_icon(obj)

    @extend_schema_field(OpenApiTypes.STR)
    def get_type_color(self, obj):
        if obj.node_type == File.NodeType.SHORTCUT:
            target = self._resolution(obj).target
            return _node_color(target) if target else _node_color(obj)
        return _node_color(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_shortcut(self, obj):
        from workspace.files.services.scanning.policy import is_blocked

        if obj.node_type != File.NodeType.SHORTCUT:
            return None
        resolution = self._resolution(obj)
        target = resolution.target
        if target is None:
            return {"state": resolution.state, "target": None}
        full_path_readable = reads_full_path(resolution.permission)
        return {
            "state": resolution.state,
            "target": {
                "uuid": str(target.uuid),
                "name": target.name,
                "node_type": target.node_type,
                # The folders above a node reached through a share are the
                # owner's: neither their names nor their uuids.
                "parent": (
                    str(target.parent_id)
                    if full_path_readable and target.parent_id
                    else None
                ),
                "path": visible_path(target, resolution.permission),
                "mime_type": target.mime_type,
                "type": target.type,
                "category": target.category,
                "size": target.size,
                "type_icon": _node_icon(target),
                "type_color": _node_color(target),
                "is_viewable": not is_blocked(target) and target.is_viewable(),
                "is_quarantined": is_blocked(target),
                "has_thumbnail": target.has_thumbnail,
            },
        }

    @extend_schema_field(OpenApiTypes.STR)
    def get_content_url(self, obj):
        from workspace.files.services.scanning.policy import is_blocked

        if is_blocked(obj):
            return None
        if obj.node_type == File.NodeType.FILE and obj.content:
            return f"/api/v1/files/{obj.uuid}/content"
        return None

    @extend_schema_field(OpenApiTypes.STR)
    def get_scan_status(self, obj):
        from workspace.files.models import FileScan
        from workspace.files.services.scanning.policy import scan_enabled

        if not scan_enabled():
            return ""
        try:
            return obj.scan.status
        except FileScan.DoesNotExist:
            return ""

    @extend_schema_field(OpenApiTypes.STR)
    def get_scan_signature(self, obj):
        from workspace.files.models import FileScan
        from workspace.files.services.scanning.policy import scan_enabled

        if not scan_enabled():
            return ""
        try:
            return obj.scan.signature
        except FileScan.DoesNotExist:
            return ""

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_quarantined(self, obj):
        from workspace.files.services.scanning.policy import is_blocked

        return is_blocked(obj)

    @extend_schema_field(
        serializers.ChoiceField(choices=[*File.ProcessingStatus.values, "quarantined"])
    )
    def get_processing_status(self, obj):
        return obj.effective_processing_status()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_pinned(self, obj):
        if obj.node_type != File.NodeType.FOLDER:
            return False
        return bool(_require_annotation(obj, "is_pinned"))

    @extend_schema_field(OpenApiTypes.STR)
    def get_path(self, obj):
        if _require_annotation(obj, "full_path_visible"):
            return obj.path
        return obj.name

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_shared(self, obj):
        return bool(_require_annotation(obj, "is_shared"))

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_has_children(self, obj):
        if obj.node_type != File.NodeType.FOLDER:
            return False
        return bool(_require_annotation(obj, "has_children"))

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_tags(self, obj):
        fts = obj.file_tags.all()  # uses prefetch cache if available
        return [
            {
                "uuid": str(ft.tag.uuid),
                "name": ft.tag.name,
                "icon": ft.tag.icon,
                "color": ft.tag.color,
                "source": ft.source,
            }
            for ft in fts
        ]

    def validate_name(self, value):
        return validate_node_name(value)

    def validate(self, attrs):
        # A request knob, never a model attribute: pop it before the
        # ModelSerializer machinery sees it. Only creation honours it; a
        # rename keeps rejecting collisions.
        on_conflict = attrs.pop("on_conflict", "error")
        if self.instance is not None:
            on_conflict = "error"
        self._on_conflict = on_conflict
        if self.instance is not None:
            errors = {}
            initial_data = self.initial_data or {}

            if "uuid" in initial_data:
                incoming_uuid = str(initial_data.get("uuid")).lower()
                if incoming_uuid != str(self.instance.uuid).lower():
                    errors["uuid"] = "This field is immutable."

            if "owner" in initial_data:
                incoming_owner = str(initial_data.get("owner"))
                if incoming_owner != str(self.instance.owner_id):
                    errors["owner"] = "This field is immutable."

            if "node_type" in initial_data:
                if str(initial_data.get("node_type")) != self.instance.node_type:
                    errors["node_type"] = "This field is immutable."

            if "group" in initial_data:
                errors["group"] = "Group cannot be changed directly."

            # Unchanged is not a move: a client may send the row back whole.
            if "parent" in attrs and attrs.pop("parent") != self.instance.parent:
                errors["parent"] = gettext(
                    "Move items through /api/v1/files/operations."
                )

            if "shortcut_target" in attrs:
                errors["shortcut_target"] = "This field is immutable."

            if self.instance.node_type == File.NodeType.SHORTCUT and "content" in attrs:
                errors["content"] = gettext("A shortcut has no content.")

            if errors:
                raise serializers.ValidationError(errors)

        node_type = attrs.get("node_type")
        instance = self.instance
        if instance is not None and node_type is None:
            node_type = instance.node_type
        if instance is None:
            self._validate_shortcut_shape(node_type, attrs, on_conflict)

        parent = attrs.get("parent")
        if (
            instance is None
            and parent is not None
            and not can_create_in(self.context["request"].user, parent)
        ):
            # Same answer as a UUID that matches no row: the refusal must not
            # reveal that someone else's folder exists.
            message = (
                self.fields["parent"]
                .error_messages["does_not_exist"]
                .format(pk_value=self.initial_data.get("parent"))
            )
            raise serializers.ValidationError({"parent": [message]})
        if instance is None:
            try:
                FileService.node_group(parent, attrs.get("group"))
            except GroupMismatch as error:
                raise serializers.ValidationError(
                    {"group": ["The group must be the parent folder's group."]}
                ) from error

        # Validate file name uniqueness via FileService
        if node_type == File.NodeType.FILE:
            name = attrs.get("name") or (instance.name if instance else None)
            parent = instance.parent if instance else attrs.get("parent")
            owner = instance.owner if instance else self.context["request"].user
            if name:
                conflict = FileService.find_name_conflict(
                    owner, parent, name, exclude_pk=instance.pk if instance else None
                )
                # A create told to rename picks the free name at its INSERT.
                if conflict is not None and on_conflict != "rename":
                    if on_conflict == "replace":
                        if attrs.get("content") is None:
                            raise serializers.ValidationError(
                                {
                                    "content": gettext(
                                        "Content is required to replace "
                                        "the existing file."
                                    )
                                }
                            )
                        self._replace_target = conflict
                    else:
                        raise serializers.ValidationError(
                            {
                                "name": gettext(
                                    "A file with the same name already "
                                    "exists in this folder."
                                )
                            }
                        )

        return super().validate(attrs)

    @staticmethod
    def _validate_shortcut_shape(node_type, attrs, on_conflict):
        """A new shortcut names a target and carries nothing else; a new file
        or folder names none."""
        if node_type != File.NodeType.SHORTCUT:
            if attrs.get("shortcut_target") is not None:
                raise serializers.ValidationError(
                    {"shortcut_target": [gettext("Only a shortcut has a target.")]}
                )
            return
        if attrs.get("shortcut_target") is None:
            raise serializers.ValidationError(
                {"shortcut_target": [gettext("A shortcut needs a target.")]}
            )
        if attrs.get("content") is not None:
            raise serializers.ValidationError(
                {"content": [gettext("A shortcut has no content.")]}
            )
        if on_conflict == "replace":
            raise serializers.ValidationError(
                {"on_conflict": [gettext("A shortcut cannot replace a file.")]}
            )
        if attrs.get("parent") is None and attrs.get("group") is not None:
            # At the root, a group node is the group's own root folder.
            raise serializers.ValidationError(
                {"group": [gettext("A shortcut goes in a folder of the group.")]}
            )

    def _create_shortcut(self, owner, validated_data):
        target = validated_data["shortcut_target"]
        try:
            with name_refusals():
                return create_shortcut(
                    owner,
                    target,
                    validated_data.get("parent"),
                    name=validated_data["name"],
                    group=validated_data.get("group"),
                    acting_user=owner,
                    keep_both=self._on_conflict == "rename",
                )
        except TargetUnreachable as error:
            # The answer a UUID matching no row gets: refusing differently
            # would confirm that a node the caller cannot open exists.
            message = (
                self.fields["shortcut_target"]
                .error_messages["does_not_exist"]
                .format(pk_value=self.initial_data.get("shortcut_target"))
            )
            raise serializers.ValidationError({"shortcut_target": [message]}) from error

    def create(self, validated_data):
        owner = self.context["request"].user
        node_type = validated_data.get("node_type")
        group = validated_data.get("group")

        replace_target = getattr(self, "_replace_target", None)
        if replace_target is not None:
            return self._replace_existing(replace_target, validated_data)

        if node_type == File.NodeType.SHORTCUT:
            instance = self._create_shortcut(owner, validated_data)
        elif node_type == File.NodeType.FOLDER:
            with name_refusals():
                instance = FileService.create_folder(
                    owner=owner,
                    name=validated_data["name"],
                    parent=validated_data.get("parent"),
                    icon=validated_data.get("icon"),
                    color=validated_data.get("color"),
                    group=group,
                    acting_user=owner,
                )
        else:
            try:
                instance = FileService.create_file(
                    owner=owner,
                    name=validated_data["name"],
                    parent=validated_data.get("parent"),
                    content=validated_data.get("content"),
                    mime_type=validated_data.get("mime_type"),
                    group=group,
                    acting_user=owner,
                    keep_both=self._on_conflict == "rename",
                )
            except NameTaken as error:
                # Free when validated, taken since by an upload that won the
                # race: replacing it is what this one asked for.
                winner = None
                if (
                    self._on_conflict == "replace"
                    and validated_data.get("content") is not None
                ):
                    winner = FileService.find_name_conflict(
                        owner, validated_data.get("parent"), validated_data["name"]
                    )
                if winner is None:
                    raise name_taken_error(error) from error
                return self._replace_existing(winner, validated_data)

        # A freshly-created node cannot have favorites, pins, shares, or
        # children yet — attach the annotations the serializer now requires
        # so the DRF `create()` response can be built without a re-fetch.
        instance.is_favorite = False
        instance.is_pinned = False
        instance.is_shared = False
        instance.has_children = False
        # Its creator owns it, in their own tree or a group they are in.
        instance.full_path_visible = True
        return instance

    def _replace_existing(self, target, validated_data):
        """Write the upload into the same-name file instead of a new row.

        Sets ``replaced_existing`` so the view can answer 200 rather than 201.
        """
        user = self.context["request"].user
        ensure_replaceable(user, target)

        instance = annotate_for_serializer(
            File.objects.filter(pk=target.pk), user
        ).first()
        with content_write_refusals():
            FileService.update_content(
                instance,
                validated_data["content"],
                name=instance.name,
                mime_type=validated_data.get("mime_type"),
                acting_user=user,
                expected_hash=self.context.get("expected_content_hash"),
            )
        self.replaced_existing = True
        return instance

    def update(self, instance, validated_data):
        # Pop content + caller-supplied mime_type so they go through FileService
        # rather than the default ModelSerializer assignment loop.
        content_provided = "content" in validated_data
        uploaded = validated_data.pop("content", None)
        explicit_mime_type = validated_data.pop("mime_type", None)
        acting_user = self.context["request"].user

        # The 423 also lives on the viewset, which is where a PATCH/PUT gets
        # its answer before any field is touched. Repeating it here is what
        # makes the rule hold for every caller of the serializer - the
        # shared-write fallback, a management command, a future endpoint.
        ensure_unlocked(acting_user, instance)

        if "name" in validated_data and validated_data["name"] != instance.name:
            _rename(acting_user, instance, validated_data["name"])

        # DRF's update() ends in a bare save(), which writes every column back
        # from an instance loaded when the request began. A PATCH that only
        # renames or recolours would then revert a lock, or the content columns
        # of a save, that landed while it was in flight - undoing from the side
        # exactly what the conditional content write refuses head-on. Write the
        # fields this request carries and leave the rest of the row alone.
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        touched = [attr for attr in validated_data if attr in _WRITABLE_COLUMNS]
        if touched:
            instance.save(update_fields=touched)

        if content_provided:
            if uploaded is None:
                # ``content: null`` destroys content as surely as bytes do, so
                # it goes through the same conditional statement rather than a
                # bare save() a lock taken meanwhile would never see.
                with content_write_refusals():
                    FileService.clear_content(
                        instance,
                        acting_user=acting_user,
                        expected_hash=self.context.get("expected_content_hash"),
                    )
            elif instance.node_type == File.NodeType.FILE:
                # The viewset answers 412 before a byte is read when the row has
                # already moved; this passes the same expectation down so the
                # database decides it, which is the half that holds under two
                # writers racing with the same expectation.
                with content_write_refusals():
                    FileService.update_content(
                        instance,
                        uploaded,
                        name=instance.name,
                        mime_type=explicit_mime_type,
                        acting_user=acting_user,
                        expected_hash=self.context.get("expected_content_hash"),
                    )
            else:
                instance.content = uploaded
                instance.size = None
                instance.content_hash = ""
                instance.save(update_fields=["content", "size", "content_hash"])
        elif explicit_mime_type is not None:
            instance.mime_type = explicit_mime_type
            instance.save(update_fields=["mime_type"])

        return instance


class FileCommentAuthorSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="pk")
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        return f"/api/v1/users/{obj.pk}/avatar"


class FileCommentSerializer(serializers.ModelSerializer):
    author = FileCommentAuthorSerializer(read_only=True)
    body_html = serializers.SerializerMethodField()

    class Meta:
        model = FileComment
        fields = [
            "uuid",
            "file",
            "author",
            "body",
            "body_html",
            "edited_at",
            "created_at",
            "deleted_at",
        ]
        read_only_fields = [
            "uuid",
            "file",
            "author",
            "body_html",
            "edited_at",
            "created_at",
            "deleted_at",
        ]

    @extend_schema_field(OpenApiTypes.STR)
    def get_body_html(self, obj):
        return render_comment_body(obj.body, self.context.get("mention_map") or {})


class FileCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField()


class FileCommentEditSerializer(serializers.Serializer):
    body = serializers.CharField()


class ImageCropSerializer(serializers.Serializer):
    """A rectangle in fractions of the rotated image (services/image_edits.py)."""

    x = serializers.FloatField(min_value=0, max_value=1)
    y = serializers.FloatField(min_value=0, max_value=1)
    width = serializers.FloatField(min_value=0, max_value=1)
    height = serializers.FloatField(min_value=0, max_value=1)


class ImageEditSerializer(serializers.Serializer):
    """Validate ``PUT /api/v1/files/<uuid>/image-edit`` into its stored form."""

    rotation = serializers.ChoiceField(choices=ROTATIONS, default=0)
    crop = ImageCropSerializer(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        problem = edit_problem(attrs["rotation"], attrs["crop"])
        if problem is not None:
            raise serializers.ValidationError(problem)
        return {"edit": normalize_edit(attrs["rotation"], attrs["crop"])}


class ImageRotateSerializer(serializers.Serializer):
    """``POST /api/v1/files/<uuid>/image-edit/rotate``: clockwise quarter turns."""

    quarter_turns = serializers.ChoiceField(choices=(-1, 1))


class FileMarksSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=MAX_ITEMS,
        help_text="Files and folders to mark or unmark.",
    )
    value = serializers.BooleanField(
        help_text="True to add the mark, false to take it off."
    )


class FileMarksResultSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.UUIDField(),
        help_text="The items that are now as asked.",
    )


class PinnedReorderSerializer(serializers.Serializer):
    """Validate the payload of ``POST /api/v1/files/pinned/reorder``.

    All checks live in ``validate_order`` so errors render as a flat list of
    strings on the ``order`` key. The orjson renderer used project-wide cannot
    serialize the int-keyed dicts that DRF's per-item ``ListField(child=...)``
    validation produces, so we cannot delegate UUID parsing to ``UUIDField``.
    """

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


class UploadSessionCreateSerializer(serializers.Serializer):
    uuid = serializers.UUIDField(
        required=False,
        help_text=(
            "Identifier the client picks for the session. Opening again under "
            "the same one answers with that session instead of a second one, so "
            "an open whose answer was lost can be retried."
        ),
    )
    name = serializers.CharField(max_length=255, help_text="Name of the file.")
    parent = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Folder the file goes in; omitted for the root.",
    )
    size = serializers.IntegerField(
        min_value=1, help_text="Size of the whole file in bytes."
    )
    on_conflict = serializers.ChoiceField(
        choices=UploadSession.OnConflict.choices,
        default=UploadSession.OnConflict.ERROR,
        help_text=(
            "What finalizing does when the name is taken, as on a regular "
            "upload: 'error', 'rename' or 'replace'."
        ),
    )
    mime_type = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Media type the client declares for the file.",
    )

    def validate_name(self, value):
        return validate_node_name(value)


class UploadSessionSerializer(serializers.ModelSerializer):
    offset = serializers.IntegerField(
        source="received",
        help_text="Bytes received so far: where the next chunk starts.",
    )
    chunk_size = serializers.SerializerMethodField(
        help_text="Bytes the client should send per chunk."
    )
    completed = serializers.SerializerMethodField(
        help_text="True once the session was finalized."
    )

    class Meta:
        model = UploadSession
        fields = [
            "uuid",
            "name",
            "parent",
            "size",
            "offset",
            "chunk_size",
            "on_conflict",
            "completed",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.INT)
    def get_chunk_size(self, obj):
        return CHUNK_SIZE

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_completed(self, obj):
        return obj.completed_at is not None


OPERATION_ERROR_MESSAGES = {
    "not_found": gettext_lazy("This item no longer exists."),
    "timeout": gettext_lazy("This item took too long and was left as it was."),
    "failed": gettext_lazy("Something went wrong with this item."),
    Refusal.NOT_ALLOWED: gettext_lazy("You are not allowed to do this with this item."),
    Refusal.TARGET: gettext_lazy("The destination folder is no longer available."),
    Refusal.INTO_ITSELF: gettext_lazy("A folder cannot go inside itself."),
    Refusal.NAME_TAKEN: gettext_lazy(
        "The destination already holds an item with this name."
    ),
    Refusal.NO_CONTENT: gettext_lazy("This file has no content to copy."),
    Refusal.NOT_REPLACEABLE: gettext_lazy("You cannot replace the existing file."),
    Refusal.LOCKED: gettext_lazy("The file is locked by someone else."),
    Refusal.STALE: gettext_lazy("The existing file changed in the meantime."),
    Refusal.QUARANTINED: gettext_lazy("File is quarantined."),
    Refusal.QUOTA: gettext_lazy("There is not enough storage space left."),
    Refusal.GROUP_ROOT: gettext_lazy("The root folder of a group stays where it is."),
    Refusal.ARCHIVE_TOO_LARGE: gettext_lazy("The archive is too large to extract."),
    Refusal.ARCHIVE_TOO_MANY_ENTRIES: gettext_lazy(
        "The archive holds too many entries to extract."
    ),
    Refusal.ARCHIVE_INVALID: gettext_lazy("The archive cannot be read."),
}


class FileOperationItemSerializer(serializers.Serializer):
    uuid = serializers.UUIDField(help_text="File or folder to act on.")
    on_conflict = serializers.ChoiceField(
        choices=[(choice.value, choice.value) for choice in OnConflict],
        required=False,
        help_text=(
            "What to do when the destination already holds the name: 'rename', "
            "'replace', 'skip' or 'error'. Defaults to 'rename' for a copy and "
            "to 'error' for a move; no other kind takes it."
        ),
    )


class FileOperationCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=FileOperation.Kind.choices)
    target = serializers.UUIDField(
        allow_null=True,
        required=False,
        help_text=(
            "Destination folder of a move, a copy or an extraction, null for "
            "the root. Required for those kinds, refused for the others."
        ),
    )
    items = FileOperationItemSerializer(
        many=True,
        max_length=MAX_ITEMS,
        required=False,
        help_text=(
            "The files and folders to act on. Emptying the trash takes none: "
            "it acts on the caller's whole trash."
        ),
    )

    def validate(self, attrs):
        kind = attrs["kind"]
        items = attrs.get("items", [])
        errors = {}
        if kind in TARGETED_KINDS:
            # A client that forgot the destination must not have its files
            # sent to the root.
            if "target" not in attrs:
                errors["target"] = [self.fields["target"].error_messages["required"]]
        elif attrs.get("target") is not None:
            errors["target"] = [gettext("This kind of operation has no destination.")]
        if kind == FileOperation.Kind.EMPTY_TRASH:
            if items:
                errors["items"] = [gettext("Emptying the trash takes no items.")]
        elif not items:
            errors["items"] = [gettext("Pick at least one item.")]
        else:
            uuids = [item["uuid"] for item in items]
            if len(set(uuids)) != len(uuids):
                errors["items"] = [gettext("An item is listed twice.")]
            elif kind not in TRANSFER_KINDS and any(
                "on_conflict" in item for item in items
            ):
                errors["items"] = [
                    gettext("Only a move or a copy takes a conflict choice.")
                ]
        if errors:
            raise serializers.ValidationError(errors)
        attrs["items"] = items
        return attrs


class FileOperationSerializer(serializers.ModelSerializer):
    target = serializers.UUIDField(
        source="target_uuid", help_text="Destination folder; null for the root."
    )
    errors = serializers.SerializerMethodField(
        help_text=(
            "The items that did not go through, each with a reason code and "
            "its message. Capped: 'counts.failed' is the real number."
        )
    )
    cancel_requested = serializers.SerializerMethodField(
        help_text="True once a cancellation was asked for."
    )

    class Meta:
        model = FileOperation
        fields = [
            "uuid",
            "kind",
            "status",
            "target",
            "target_name",
            "total",
            "processed",
            "bytes_total",
            "bytes_done",
            "counts",
            "errors",
            "cancel_requested",
            "created_at",
            "finished_at",
        ]
        read_only_fields = fields

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "uuid": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        }
    )
    def get_errors(self, obj):
        return [
            {
                **error,
                "message": str(
                    OPERATION_ERROR_MESSAGES.get(
                        error["reason"], OPERATION_ERROR_MESSAGES["failed"]
                    )
                ),
            }
            for error in obj.errors
        ]

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_cancel_requested(self, obj):
        return obj.cancel_requested_at is not None
