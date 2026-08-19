from rest_framework import serializers

from .models import ImportConnection
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


class BrowseQuerySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[KIND_FILES], default=KIND_FILES)
    path = serializers.CharField(required=False, allow_blank=True, default="")
