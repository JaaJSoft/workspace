from rest_framework import serializers

from .models import Calendar, Event, EventMember


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class CalendarSerializer(serializers.ModelSerializer):
    owner = MemberUserSerializer()
    is_synced = serializers.SerializerMethodField()

    class Meta:
        model = Calendar
        fields = ['uuid', 'name', 'color', 'owner', 'is_synced', 'created_at']

    def get_is_synced(self, obj):
        return obj.mail_account_id is not None


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
    is_recurring = serializers.BooleanField(read_only=True)
    is_exception = serializers.BooleanField(read_only=True)
    poll_id = serializers.SerializerMethodField()
    ical_uid = serializers.CharField(read_only=True)
    organizer_email = serializers.EmailField(read_only=True)

    class Meta:
        model = Event
        fields = [
            'uuid', 'calendar_id', 'title', 'description', 'start', 'end',
            'all_day', 'location', 'owner', 'members',
            'recurrence_frequency', 'recurrence_interval', 'recurrence_end',
            'is_recurring', 'is_exception', 'poll_id',
            'ical_uid', 'organizer_email',
            'created_at', 'updated_at',
        ]

    def get_poll_id(self, obj):
        poll_id = getattr(obj, '_poll_id', None)
        return str(poll_id) if poll_id else None


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
    recurrence_frequency = serializers.ChoiceField(
        choices=Event.RecurrenceFrequency.choices,
        required=False, allow_null=True, default=None,
    )
    recurrence_interval = serializers.IntegerField(
        required=False, default=1, min_value=1,
    )
    recurrence_end = serializers.DateTimeField(
        required=False, allow_null=True, default=None,
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
    recurrence_frequency = serializers.ChoiceField(
        choices=Event.RecurrenceFrequency.choices,
        required=False, allow_null=True,
    )
    recurrence_interval = serializers.IntegerField(
        required=False, min_value=1,
    )
    recurrence_end = serializers.DateTimeField(
        required=False, allow_null=True,
    )
    scope = serializers.ChoiceField(
        choices=['this', 'future', 'all'],
        required=False, default='all',
    )
    original_start = serializers.DateTimeField(required=False)


class OccurrenceSerializer(serializers.Serializer):
    """Serializes virtual occurrence dicts (not backed by a model instance)."""
    uuid = serializers.CharField()
    calendar_id = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    start = serializers.CharField()
    end = serializers.CharField(allow_null=True)
    all_day = serializers.BooleanField()
    location = serializers.CharField()
    owner = MemberUserSerializer()
    members = EventMemberSerializer(many=True)
    created_at = serializers.CharField()
    updated_at = serializers.CharField()
    is_recurring = serializers.BooleanField()
    is_exception = serializers.BooleanField()
    master_event_id = serializers.CharField()
    original_start = serializers.CharField(allow_null=True)
    recurrence_frequency = serializers.CharField(allow_null=True)
    recurrence_interval = serializers.IntegerField()
    recurrence_end = serializers.CharField(allow_null=True)


class EventRespondSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['accepted', 'declined'])
