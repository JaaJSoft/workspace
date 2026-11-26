from django.urls import reverse
from rest_framework import serializers


class UserSummarySerializer(serializers.Serializer):
    """A user as every API answer shows one: an author, an assignee, a
    member, an actor. Enough to draw a name and an avatar without a second
    request - never a module's own variant of it."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, user) -> str:
        return reverse("user-avatar-retrieve", args=[user.pk])
