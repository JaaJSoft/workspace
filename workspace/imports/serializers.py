from rest_framework import serializers

from .models import ImportConnection, ImportJob, ImportJobItem
from .providers.base import KIND_FILES
from .providers.registry import provider_registry


class ConnectionSerializer(serializers.ModelSerializer):
    provider_name = serializers.SerializerMethodField()
    has_secret = serializers.SerializerMethodField()

    class Meta:
        model = ImportConnection
        fields = [
            "uuid",
            "provider",
            "provider_name",
            "label",
            "base_url",
            "username",
            "has_secret",
            "capabilities",
            "last_checked_at",
            "last_error",
            "created_at",
            "updated_at",
        ]

    def get_provider_name(self, obj):
        provider = provider_registry.get(obj.provider)
        return provider.name if provider else obj.provider

    def get_has_secret(self, obj):
        return bool(obj.secret_encrypted)


class ConnectionCreateSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    label = serializers.CharField(max_length=255)
    base_url = serializers.URLField(max_length=2000)
    username = serializers.CharField(max_length=255)
    secret = serializers.CharField(max_length=1000, trim_whitespace=False)

    def validate_provider(self, value):
        provider = provider_registry.get(value)
        if provider is None or not provider.is_available():
            raise serializers.ValidationError("Unknown provider.")
        if provider.auth != "credentials":
            raise serializers.ValidationError(
                "This provider is connected through its sign-in flow, not a password."
            )
        return value


class ConnectionUpdateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=255, required=False)
    base_url = serializers.URLField(max_length=2000, required=False)
    username = serializers.CharField(max_length=255, required=False)
    secret = serializers.CharField(
        max_length=1000, required=False, trim_whitespace=False
    )


class RemotePathField(serializers.CharField):
    """A path inside the remote tree, normalised to ``/a/b`` (``/`` for the
    root). Dot segments are refused: the HTTP client would collapse them and
    the request could leave the DAV root the connection was vetted for."""

    default_error_messages = {
        "dot_segments": "The path must not contain '.' or '..' segments."
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("default", "/")
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        segments = [s for s in value.split("/") if s]
        if any(s in (".", "..") for s in segments):
            self.fail("dot_segments")
        return "/" + "/".join(segments) if segments else "/"


class BrowseQuerySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[KIND_FILES], default=KIND_FILES)
    path = RemotePathField()


class JobSerializer(serializers.ModelSerializer):
    connection = serializers.UUIDField(source="connection_id")
    connection_label = serializers.CharField(source="connection.label")

    class Meta:
        model = ImportJob
        fields = [
            "uuid",
            "connection",
            "connection_label",
            "kinds",
            "options",
            "status",
            "stats",
            "error",
            "cancel_requested_at",
            "created_at",
            "started_at",
            "finished_at",
        ]


class JobCreateSerializer(serializers.Serializer):
    connection = serializers.UUIDField()
    kinds = serializers.ListField(
        child=serializers.CharField(max_length=30), allow_empty=False
    )
    options = serializers.DictField(required=False, default=dict)


class JobItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJobItem
        fields = [
            "uuid",
            "kind",
            "remote_id",
            "status",
            "target_uuid",
            "error",
            "created_at",
        ]


class PageQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=500, default=100)
    offset = serializers.IntegerField(min_value=0, default=0)


class JobItemsQuerySerializer(PageQuerySerializer):
    status = serializers.ChoiceField(
        choices=ImportJobItem.Status.choices, required=False
    )
