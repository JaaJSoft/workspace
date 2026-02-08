from rest_framework import serializers

from workspace.users.avatar_service import has_avatar

from .models import Calendar, Event, EventMember


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, user):
        if has_avatar(user):
            return f'/api/v1/users/{user.id}/avatar'
        return None


class CalendarSerializer(serializers.ModelSerializer):
    owner = MemberUserSerializer()

    class Meta:
        model = Calendar
        fields = ['uuid', 'name', 'color', 'owner', 'created_at']


class CalendarCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    color = serializers.CharField(max_length=30, required=False, default='primary')


class EventMemberSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer()

    class Meta:
        model = EventMember
        fields = ['uuid', 'user', 'status', 'created_at']


class EventSerializer(serializers.ModelSerializer):
    owner = MemberUserSerializer()
    members = EventMemberSerializer(many=True, read_only=True)
    calendar_id = serializers.UUIDField(source='calendar.uuid', read_only=True)

    class Meta:
        model = Event
        fields = [
            'uuid', 'calendar_id', 'title', 'description', 'start', 'end',
            'all_day', 'location', 'owner', 'members',
            'created_at', 'updated_at',
        ]


class EventCreateSerializer(serializers.Serializer):
    calendar_id = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default='', allow_blank=True)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField(required=False, allow_null=True, default=None)
    all_day = serializers.BooleanField(required=False, default=False)
    location = serializers.CharField(max_length=255, required=False, default='', allow_blank=True)
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )


class EventUpdateSerializer(serializers.Serializer):
    calendar_id = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False, allow_null=True)
    all_day = serializers.BooleanField(required=False)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
    )


class EventRespondSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['accepted', 'declined'])
