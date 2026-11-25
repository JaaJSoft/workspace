from rest_framework import serializers

from workspace.common.url_guard import UnsafeUrl, check_remote_url

from .models_external import ExternalCalendar


class ExternalCalendarCreateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048)
    name = serializers.CharField(max_length=255)
    color = serializers.CharField(max_length=30, required=False, default="primary")

    def validate_url(self, value):
        # A host name is only resolved when the feed is fetched; this refuses
        # what is off limits without a lookup (an internal address typed in).
        try:
            check_remote_url(value)
        except UnsafeUrl as exc:
            raise serializers.ValidationError(exc.user_message) from None
        return value


class ExternalCalendarUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    color = serializers.CharField(max_length=30, required=False)


class ExternalSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalCalendar
        fields = [
            "uuid",
            "url",
            "last_synced_at",
            "last_error",
            "is_active",
        ]


class ExternalCalendarSerializer(serializers.Serializer):
    """Combined view: Calendar fields + external source details."""

    uuid = serializers.UUIDField(source="calendar.uuid")
    name = serializers.CharField(source="calendar.name")
    color = serializers.CharField(source="calendar.color")
    is_external = serializers.SerializerMethodField()
    external_source = ExternalSourceSerializer(source="*")
    created_at = serializers.DateTimeField(source="calendar.created_at")

    def get_is_external(self, obj):
        return True
