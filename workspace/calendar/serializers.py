from datetime import UTC, datetime

from rest_framework import serializers

from .models import Calendar, Event, EventMember
from .recurrence import meeting_join_url
from .services import recurrence_rule
from .services.timezones import normalize_all_day


class FlexibleDateTimeField(serializers.DateTimeField):
    """DateTimeField that also accepts date-only strings as UTC midnight.

    All-day values travel as 'YYYY-MM-DD' day labels; anchoring them at UTC
    midnight here keeps the storage invariant without a separate field type.
    """

    def to_internal_value(self, value):
        if isinstance(value, str) and len(value) == 10:
            try:
                return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                pass
        return super().to_internal_value(value)


class AllDayNormalizingMixin:
    """Truncates all-day start/end to UTC midnight during validation."""

    def validate(self, attrs):
        if attrs.get("all_day"):
            if attrs.get("start"):
                attrs["start"] = normalize_all_day(attrs["start"])
            if attrs.get("end"):
                attrs["end"] = normalize_all_day(attrs["end"])
        return attrs


class RecurrenceRuleValidationMixin:
    """Rejects recurrence text the expansion engine cannot read.

    Stored text nothing can parse becomes a master with no bound, which no
    window query prunes: it is loaded and logged on every calendar read, for
    every window, for every user who can see it. Refusing it at the boundary
    is cheaper than containing it afterwards.
    """

    # Parseability is a property of the text, not of when the series starts,
    # so any aware instant does as the anchor - a fixed one keeps the check
    # deterministic.
    _VALIDATION_ANCHOR = datetime(2000, 1, 1, tzinfo=UTC)

    def validate_recurrence_rule(self, value):
        if value and recurrence_rule.parse(value, self._VALIDATION_ANCHOR) is None:
            raise serializers.ValidationError("Unrecognized recurrence rule.")
        return value


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class CalendarSerializer(serializers.ModelSerializer):
    owner = MemberUserSerializer()
    is_synced = serializers.SerializerMethodField()
    is_external = serializers.SerializerMethodField()

    class Meta:
        model = Calendar
        fields = [
            "uuid",
            "name",
            "color",
            "owner",
            "is_synced",
            "is_external",
            "created_at",
        ]

    def get_is_synced(self, obj):
        return obj.mail_account_id is not None

    def get_is_external(self, obj):
        return hasattr(obj, "external_source")


class CalendarCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    color = serializers.CharField(max_length=30, required=False, default="primary")


class EventMemberSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer()

    class Meta:
        model = EventMember
        fields = ["uuid", "user", "status", "created_at"]


class EventSerializer(serializers.ModelSerializer):
    owner = MemberUserSerializer()
    members = EventMemberSerializer(many=True, read_only=True)
    calendar_id = serializers.UUIDField(source="calendar.uuid", read_only=True)
    is_recurring = serializers.BooleanField(read_only=True)
    is_exception = serializers.BooleanField(read_only=True)
    poll_id = serializers.SerializerMethodField()
    ical_uid = serializers.CharField(read_only=True)
    external_organizer = serializers.EmailField(read_only=True)
    join_url = serializers.SerializerMethodField()
    recurrence_summary = serializers.SerializerMethodField()
    recurrence_simple = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "uuid",
            "calendar_id",
            "title",
            "description",
            "start",
            "end",
            "all_day",
            "timezone",
            "location",
            "join_url",
            "owner",
            "members",
            "recurrence_rule",
            "recurrence_summary",
            "recurrence_simple",
            "is_recurring",
            "is_exception",
            "poll_id",
            "ical_uid",
            "external_organizer",
            "created_at",
            "updated_at",
        ]

    def get_poll_id(self, obj):
        poll_id = getattr(obj, "_poll_id", None)
        return str(poll_id) if poll_id else None

    def get_join_url(self, obj):
        # A materialized exception never legitimately owns a Meeting - see
        # meeting_join_url's docstring - so its own row never carries the
        # join link; read it through the series master.
        return meeting_join_url(
            obj.recurrence_parent or obj, self.context.get("request")
        )

    def get_recurrence_summary(self, obj):
        return recurrence_rule.describe(obj.recurrence_rule)

    def get_recurrence_simple(self, obj):
        """Picker-shaped view, or None when the rule is beyond the picker.

        None is the signal the web modal uses to go read-only; deriving it
        server-side keeps a second RRULE parser out of the frontend.
        """
        return recurrence_rule.to_simple_json(obj.recurrence_rule)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("all_day"):
            # All-day values are UTC-midnight day labels: expose them
            # date-only so no client can shift them across zones. Read the
            # instance datetimes - the rendered strings carry the active
            # timezone's offset, so slicing them would shift the label.
            if instance.start:
                data["start"] = instance.start.astimezone(UTC).date().isoformat()
            if instance.end:
                data["end"] = instance.end.astimezone(UTC).date().isoformat()
        return data


class EventCreateSerializer(
    RecurrenceRuleValidationMixin, AllDayNormalizingMixin, serializers.Serializer
):
    calendar_id = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="", allow_blank=True)
    start = FlexibleDateTimeField()
    end = FlexibleDateTimeField(required=False, allow_null=True, default=None)
    all_day = serializers.BooleanField(required=False, default=False)
    location = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )
    recurrence_rule = serializers.CharField(
        required=False, allow_blank=True, default="", trim_whitespace=False
    )


class EventUpdateSerializer(
    RecurrenceRuleValidationMixin, AllDayNormalizingMixin, serializers.Serializer
):
    calendar_id = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    start = FlexibleDateTimeField(required=False)
    end = FlexibleDateTimeField(required=False, allow_null=True)
    all_day = serializers.BooleanField(required=False)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
    )
    recurrence_rule = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    scope = serializers.ChoiceField(
        choices=["this", "future", "all"],
        required=False,
        default="all",
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
    recurrence_rule = serializers.CharField(allow_blank=True)
    recurrence_summary = serializers.CharField(allow_blank=True)
    recurrence_simple = serializers.DictField(allow_null=True)


class EventRespondSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["accepted", "declined"])
