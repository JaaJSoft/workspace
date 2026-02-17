from rest_framework import serializers


class NotificationSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    origin = serializers.CharField()
    icon = serializers.CharField()
    color = serializers.CharField()
    priority = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    url = serializers.CharField()
    actor = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_actor(self, obj):
        if obj.actor:
            return {'id': obj.actor_id, 'username': obj.actor.username}
        return None

    def get_is_read(self, obj):
        return obj.read_at is not None
