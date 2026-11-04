import re
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.urls import reverse
from django.utils.translation import gettext as _
from rest_framework import serializers

from workspace.mail.models import MailAccount
from workspace.mail.queries import user_account_ids

from .models import Booking, BookingPage, Calendar
from .queries import owned_calendar_ids

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RANGES_PER_DAY = 6


def validate_zone_name(value):
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError, ValueError, KeyError:
        raise serializers.ValidationError(_("Unknown time zone.")) from None
    return value


def validate_weekly_hours(value):
    """Seven lists, Monday first, of sorted, disjoint ``["HH:MM", "HH:MM"]`` ranges."""
    if not isinstance(value, list) or len(value) != 7:
        raise serializers.ValidationError(_("Give the hours of all seven days."))
    days = []
    for ranges in value:
        if not isinstance(ranges, list) or len(ranges) > MAX_RANGES_PER_DAY:
            raise serializers.ValidationError(
                _("A day has at most %(count)d time ranges.")
                % {"count": MAX_RANGES_PER_DAY}
            )
        day = []
        for item in ranges:
            if not (
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(t, str) and _HHMM.match(t) for t in item)
            ):
                raise serializers.ValidationError(
                    _("Times are written HH:MM, in 24-hour format.")
                )
            if item[0] >= item[1]:
                raise serializers.ValidationError(
                    _("A time range must end after it starts.")
                )
            day.append([item[0], item[1]])
        day.sort()
        if any(later[0] < earlier[1] for earlier, later in pairwise(day)):
            raise serializers.ValidationError(
                _("The time ranges of a day must not overlap.")
            )
        days.append(day)
    return days


def booking_page_url(request, page):
    path = reverse("calendar_ui:booking-page", args=[page.token])
    return request.build_absolute_uri(path) if request else path


class BookingPageSerializer(serializers.ModelSerializer):
    """A booking page as its owner edits it."""

    calendar = serializers.PrimaryKeyRelatedField(queryset=Calendar.objects.none())
    busy_calendars = serializers.PrimaryKeyRelatedField(
        queryset=Calendar.objects.none(), many=True, required=False
    )
    mail_account = serializers.PrimaryKeyRelatedField(
        queryset=MailAccount.objects.none(), allow_null=True, required=False
    )
    weekly_hours = serializers.JSONField(required=False)
    timezone = serializers.CharField(
        max_length=64, required=False, validators=[validate_zone_name]
    )
    share_url = serializers.SerializerMethodField()
    upcoming_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = BookingPage
        fields = [
            "uuid",
            "title",
            "description",
            "location",
            "calendar",
            "busy_calendars",
            "duration_minutes",
            "buffer_minutes",
            "min_notice_hours",
            "days_ahead",
            "weekly_hours",
            "timezone",
            "mail_account",
            "is_active",
            "token",
            "share_url",
            "upcoming_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "token", "created_at", "updated_at"]
        extra_kwargs = {
            "duration_minutes": {"min_value": 5, "max_value": 480},
            "buffer_minutes": {"min_value": 0, "max_value": 240},
            "min_notice_hours": {"min_value": 0, "max_value": 720},
            "days_ahead": {"min_value": 1, "max_value": 365},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is None:
            return
        user = request.user
        owned = Calendar.objects.filter(uuid__in=owned_calendar_ids(user))
        # An external calendar is rewritten by every feed sync: an appointment
        # put there would vanish at the next one.
        self.fields["calendar"].queryset = owned.filter(external_source__isnull=True)
        self.fields["busy_calendars"].child_relation.queryset = owned
        self.fields["mail_account"].queryset = MailAccount.objects.filter(
            uuid__in=user_account_ids(user)
        )

    def validate_weekly_hours(self, value):
        return validate_weekly_hours(value)

    def get_share_url(self, page):
        return booking_page_url(self.context.get("request"), page)


class BookingPageRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingPage
        fields = ["uuid", "title"]


class BookingSerializer(serializers.ModelSerializer):
    """A booking as the page owner sees it.

    The guest's ``token`` is their credential and never leaves for anyone
    else, the owner included.
    """

    page = BookingPageRefSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "uuid",
            "page",
            "start",
            "end",
            "guest_name",
            "guest_email",
            "note",
            "status",
            "cancelled_by",
            "cancelled_at",
            "event_id",
            "created_at",
        ]
        read_only_fields = fields


def public_page_data(page):
    """What a visitor of the public link learns about the page: no calendar,
    no account, no rule beyond what picking a slot needs."""
    return {
        "token": page.token,
        "title": page.title,
        "description": page.description,
        "location": page.location,
        "duration_minutes": page.duration_minutes,
        "owner_name": page.owner.get_full_name() or page.owner.username,
        "days_ahead": page.days_ahead,
    }


class BookRequestSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
    name = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    note = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    timezone = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )

    def validate_timezone(self, value):
        if not value:
            return ""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError, KeyError:
            return ""
        return value
