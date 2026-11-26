from rest_framework import serializers

from workspace.common.serializers import ApiModelSerializer
from workspace.common.url_guard import UnsafeUrl
from workspace.users.serializers import UserSummarySerializer

from .models import Notification, PushSubscription
from .services.push_transport import check_push_endpoint


class NotificationSerializer(ApiModelSerializer):
    actor = UserSummarySerializer(read_only=True, allow_null=True)
    is_read = serializers.BooleanField()

    class Meta:
        model = Notification
        fields = [
            "uuid",
            "origin",
            "icon",
            "priority",
            "title",
            "body",
            "url",
            "actor",
            "is_read",
            "created_at",
        ]
        read_only_fields = [
            "uuid",
            "origin",
            "icon",
            "priority",
            "title",
            "body",
            "url",
            "created_at",
        ]


class PushKeySerializer(serializers.Serializer):
    public_key = serializers.CharField(
        allow_blank=True,
        help_text="The VAPID public key a browser subscribes with; empty when push is not configured.",
    )


class PushKeysSerializer(serializers.Serializer):
    p256dh = serializers.CharField(max_length=200)
    auth = serializers.CharField(max_length=100)


class PushSubscriptionSerializer(ApiModelSerializer):
    # Declared by hand to drop the model's uniqueness check: registering an
    # endpoint that already has a row refreshes it.
    endpoint = serializers.URLField(
        max_length=500, help_text="Push service endpoint URL."
    )
    keys = PushKeysSerializer(write_only=True)

    class Meta:
        model = PushSubscription
        fields = ["uuid", "endpoint", "keys", "created_at"]
        read_only_fields = ["uuid", "created_at"]

    def validate_endpoint(self, endpoint):
        try:
            check_push_endpoint(endpoint)
        except UnsafeUrl as exc:
            raise serializers.ValidationError(exc.user_message) from exc
        return endpoint
