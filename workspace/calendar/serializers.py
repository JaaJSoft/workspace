from datetime import UTC, datetime

from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from workspace.common.serializers import ApiModelSerializer, InstantField
from workspace.common.url_guard import UnsafeUrl, check_remote_url
from workspace.users.serializers import UserSummarySerializer

from .models import Calendar, Event, EventMember
from .queries import visible_calendar_ids
from .services import recurrence_rule
from .services.calendars import external_source
from .services.timezones import normalize_all_day


def viewer(context):
    """Whoever the answer is for: the caller of the request, or the user a
    task or a tool serializes for."""
    return context.get("viewer") or context["request"].user


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
            raise serializers.ValidationError(_("Unrecognized recurrence rule."))
        return value


class CalendarSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.CharField()


class CalendarSerializer(ApiModelSerializer):
    owner = UserSummarySerializer(read_only=True)
    is_owned = serializers.SerializerMethodField()
    is_synced = serializers.SerializerMethodField()
    is_external = serializers.SerializerMethodField()
    source_url = serializers.URLField(
        source="external_source.url",
        max_length=2048,
        required=False,
        allow_null=True,
        help_text=(
            "The ICS feed an external calendar is filled from. Set when the "
            "calendar is created, and never changed afterwards."
        ),
    )
    last_synced_at = serializers.SerializerMethodField()
    sync_error = serializers.SerializerMethodField()

    class Meta:
        model = Calendar
        fields = [
            "uuid",
            "name",
            "color",
            "owner",
            "is_owned",
            "is_synced",
            "is_external",
            "source_url",
            "last_synced_at",
            "sync_error",
            "created_at",
            "updated_at",
        ]

    def get_is_owned(self, calendar) -> bool:
        return calendar.owner_id == viewer(self.context).id

    def get_is_synced(self, calendar) -> bool:
        """Whether a mail account files the invitations it receives here."""
        return calendar.mail_account_id is not None

    def get_is_external(self, calendar) -> bool:
        return external_source(calendar) is not None

    @extend_schema_field(InstantField(allow_null=True))
    def get_last_synced_at(self, calendar):
        source = external_source(calendar)
        if source is None or source.last_synced_at is None:
            return None
        return InstantField().to_representation(source.last_synced_at)

    def get_sync_error(self, calendar) -> str:
        source = external_source(calendar)
        return source.last_error if source is not None else ""

    def validate_source_url(self, value):
        if not value:
            return None
        # A host name is only resolved when the feed is fetched; this refuses
        # what is off limits without a lookup (an internal address typed in).
        try:
            check_remote_url(value)
        except UnsafeUrl as exc:
            raise serializers.ValidationError(exc.user_message) from None
        return value

    def validate(self, attrs):
        asked = attrs.pop("external_source", None)
        if asked is not None:
            attrs["source_url"] = asked["url"]
        if self.instance is not None and "source_url" in attrs:
            source = external_source(self.instance)
            if attrs.pop("source_url") != (source.url if source else None):
                raise serializers.ValidationError(
                    {"source_url": [_("The feed of a calendar cannot be changed.")]}
                )
        return attrs


class CalendarFeedSerializer(serializers.Serializer):
    url = serializers.URLField(
        allow_null=True,
        help_text="The address other apps subscribe to; null while the feed is off.",
    )


class EventMemberSerializer(serializers.Serializer):
    user = UserSummarySerializer()
    status = serializers.ChoiceField(choices=EventMember.Status.choices)


class GuestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )


class EventGuestSerializer(GuestSerializer):
    status = serializers.ChoiceField(choices=EventMember.Status.choices)


class PollSummarySerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    title = serializers.CharField()


def series_of(event):
    """The row that holds the recurrence rule: the event itself, or the
    series an occurrence changed on its own was split from."""
    return event.recurrence_parent if event.recurrence_parent_id else event


class EventSerializer(ApiModelSerializer):
    """An event, or one occurrence of a recurring one.

    An occurrence is named by the uuid of its series and its
    ``original_start``, the slot the rule gives it, whether or not it was
    changed on its own since.
    """

    uuid = serializers.SerializerMethodField()
    calendar = serializers.SerializerMethodField()
    owner = UserSummarySerializer(read_only=True)
    members = serializers.SerializerMethodField()
    guests = serializers.SerializerMethodField()
    is_all_day = serializers.BooleanField(source="all_day", read_only=True)
    is_recurring = serializers.SerializerMethodField()
    is_exception = serializers.BooleanField(read_only=True)
    recurrence_rule = serializers.SerializerMethodField()
    recurrence_summary = serializers.SerializerMethodField()
    recurrence_simple = serializers.SerializerMethodField()
    poll = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "uuid",
            "calendar",
            "title",
            "description",
            "start",
            "end",
            "is_all_day",
            "timezone",
            "location",
            "owner",
            "members",
            "guests",
            "recurrence_rule",
            "recurrence_summary",
            "recurrence_simple",
            "is_recurring",
            "is_exception",
            "original_start",
            "poll",
            "ical_uid",
            "external_organizer",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "start": {"help_text": "An instant, or a date alone for an all-day event."},
            "end": {
                "help_text": (
                    "An instant, or for an all-day event the day after its last one."
                )
            },
            "original_start": {
                "help_text": (
                    "The slot of this occurrence in its series; null for the "
                    "series itself and for an event that does not repeat."
                )
            },
        }

    def _visible_calendars(self):
        # The context is one dict for every row of a list.
        if "visible_calendar_ids" not in self.context:
            self.context["visible_calendar_ids"] = set(
                visible_calendar_ids(viewer(self.context))
            )
        return self.context["visible_calendar_ids"]

    @extend_schema_field(OpenApiTypes.UUID)
    def get_uuid(self, event):
        return str(event.recurrence_parent_id or event.uuid)

    @extend_schema_field(CalendarSummarySerializer(allow_null=True))
    def get_calendar(self, event):
        # An invitee reaches the event through its membership: the calendar
        # the organizer filed it in is not theirs to see.
        if event.calendar_id not in self._visible_calendars():
            return None
        return CalendarSummarySerializer(event.calendar).data

    @extend_schema_field(EventMemberSerializer(many=True))
    def get_members(self, event):
        accounts = [m for m in event.members.all() if m.user_id is not None]
        return EventMemberSerializer(accounts, many=True, context=self.context).data

    @extend_schema_field(EventGuestSerializer(many=True))
    def get_guests(self, event):
        guests = [m for m in event.members.all() if m.user_id is None]
        return EventGuestSerializer(guests, many=True).data

    def get_is_recurring(self, event) -> bool:
        return event.is_recurring or event.recurrence_parent_id is not None

    def get_recurrence_rule(self, event) -> str:
        return series_of(event).recurrence_rule

    def _described(self, event):
        """(summary, picker view) of the rule of *event*'s series.

        Both are pure functions of the rule text, and a page of a list holds
        the same series many times over, so each text is read once.
        """
        rule = series_of(event).recurrence_rule
        described = self.context.setdefault("described_rules", {})
        if rule not in described:
            described[rule] = (
                recurrence_rule.describe(rule),
                recurrence_rule.to_simple_json(rule),
            )
        return described[rule]

    def get_recurrence_summary(self, event) -> str:
        return self._described(event)[0]

    @extend_schema_field(
        {"type": "object", "nullable": True, "additionalProperties": True}
    )
    def get_recurrence_simple(self, event):
        """Picker-shaped view, or None when the rule is beyond the picker.

        None is the signal the web modal uses to go read-only; deriving it
        server-side keeps a second RRULE parser out of the frontend.
        """
        return self._described(event)[1]

    @extend_schema_field(PollSummarySerializer(allow_null=True))
    def get_poll(self, event):
        poll_uuid = getattr(event, "_poll_uuid", None)
        if poll_uuid is None:
            return None
        return {"uuid": str(poll_uuid), "title": event._poll_title}

    def to_representation(self, event):
        data = super().to_representation(event)
        if event.all_day:
            # All-day values are UTC-midnight day labels: expose them
            # date-only so no client can shift them across zones.
            data["start"] = event.start.astimezone(UTC).date().isoformat()
            if event.end:
                data["end"] = event.end.astimezone(UTC).date().isoformat()
        return data


class OwnCalendarField(serializers.PrimaryKeyRelatedField):
    """A calendar the caller may write events into: one of their own."""

    def get_queryset(self):
        return Calendar.objects.filter(owner=viewer(self.context)).select_related(
            "external_source"
        )


class EventWriteSerializer(RecurrenceRuleValidationMixin, serializers.Serializer):
    calendar = OwnCalendarField(queryset=Calendar.objects.all())
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    start = FlexibleDateTimeField(
        help_text="An instant, or a date alone for an all-day event."
    )
    end = FlexibleDateTimeField(
        required=False,
        allow_null=True,
        help_text="An instant, or for an all-day event the day after its last one.",
    )
    is_all_day = serializers.BooleanField(source="all_day", required=False)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    members = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text=(
            "The ids of the accounts invited: the whole list, replacing the "
            "one the event had."
        ),
    )
    guests = GuestSerializer(
        many=True,
        required=False,
        help_text="The people invited by email: the whole list, as for `members`.",
    )
    recurrence_rule = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )

    def validate_calendar(self, calendar):
        if external_source(calendar) is not None:
            raise serializers.ValidationError(
                _("Cannot create events in an external calendar.")
            )
        return calendar

    def validate(self, attrs):
        if attrs.get("all_day"):
            if attrs.get("start"):
                attrs["start"] = normalize_all_day(attrs["start"])
            if attrs.get("end"):
                attrs["end"] = normalize_all_day(attrs["end"])
        return attrs


class OccurrenceQuerySerializer(serializers.Serializer):
    original_start = serializers.DateTimeField(
        required=False,
        help_text=(
            "The slot of one occurrence of a recurring event, as its "
            "`original_start` reads."
        ),
    )


SERIES_PARTS = ("this", "future", "all")


class SeriesWriteQuerySerializer(OccurrenceQuerySerializer):
    occurrences = serializers.ChoiceField(
        choices=SERIES_PARTS,
        required=False,
        help_text=(
            "Required on a recurring event: `this` occurrence, this one and "
            "the `future` ones, or `all` of the series. `this` and `future` "
            "take `original_start`."
        ),
    )


class EventResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[EventMember.Status.ACCEPTED, EventMember.Status.DECLINED]
    )


class InviteeResolveSerializer(serializers.Serializer):
    persons = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    lists = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )

    def validate(self, attrs):
        if not attrs["persons"] and not attrs["lists"]:
            raise serializers.ValidationError(
                _("Name at least one person or one list.")
            )
        return attrs


class SkippedPersonSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    display_name = serializers.CharField()


class ResolvedInviteesSerializer(serializers.Serializer):
    users = UserSummarySerializer(many=True)
    guests = GuestSerializer(many=True)
    skipped = SkippedPersonSerializer(many=True)
