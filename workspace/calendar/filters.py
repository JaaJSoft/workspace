"""The filters of the module's lists."""

from django import forms
from django.db.models import Q
from django.utils.functional import cached_property
from django_filters import rest_framework as filters

from workspace.common.filters import (
    BooleanFilter,
    KeyFilter,
    MultiValueFilter,
    OrderingFilter,
    TextSearchFilter,
    without_none,
)

from .models import Calendar, Event, Poll
from .queries import undeclined_events, visible_calendar_ids


class CalendarFilter(filters.FilterSet):
    q = TextSearchFilter(fields=("name",))
    external = BooleanFilter(
        field_name="external_source", lookup_expr="isnull", exclude=True
    )
    owner = MultiValueFilter(child=forms.IntegerField(), allow_me=True)
    owner_username = KeyFilter(field_name="owner__username")

    class Meta:
        model = Calendar
        fields = []


class EventFilter(filters.FilterSet):
    """What ``GET /events`` takes.

    The list is no queryset - series are expanded into occurrences - so
    ``start`` and ``end`` are read by the view and handed to
    ``services.event_listing``, which narrows its own querysets through
    ``filter_queryset``.
    """

    start = filters.IsoDateTimeFilter(
        method="read_by_the_listing",
        help_text=(
            "Alone, lists what starts from this instant on, all-day events "
            "of its day included. Now by default."
        ),
    )
    end = filters.IsoDateTimeFilter(
        method="read_by_the_listing",
        help_text="With it, lists what overlaps the window from `start` to `end`.",
    )
    calendar = MultiValueFilter(
        child=forms.UUIDField(),
        allow_none=True,
        method="in_calendars",
        help_text=(
            "`none` stands for the events filed in no calendar of the "
            "caller's: the ones they are invited to."
        ),
    )
    calendar_name = KeyFilter(method="in_calendars_named")
    include_declined = BooleanFilter(
        method="read_by_the_listing",
        help_text="true to list the events the caller declined as well.",
    )

    class Meta:
        model = Event
        fields = []

    def read_by_the_listing(self, queryset, name, value):
        return queryset

    # The listing narrows several querysets with one filter set: what the
    # caller can see is read once.
    @cached_property
    def _visible_calendars(self):
        return visible_calendar_ids(self.request.user)

    def in_calendars(self, queryset, name, value):
        # A calendar the caller cannot see holds nothing for them, even the
        # events of it they are invited to: those are filed under `none`.
        asked, with_none = without_none(value)
        visible = self._visible_calendars
        condition = Q(calendar_id__in=[uuid for uuid in visible if uuid in set(asked)])
        if with_none:
            condition |= ~Q(calendar_id__in=visible)
        return queryset.filter(condition)

    def in_calendars_named(self, queryset, name, value):
        named = Calendar.objects.filter(
            KeyFilter(field_name="name").condition(value),
            uuid__in=self._visible_calendars,
        )
        return queryset.filter(calendar_id__in=named.values("uuid"))

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if not self.form.cleaned_data.get("include_declined"):
            queryset = undeclined_events(queryset, self.request.user)
        return queryset


POLL_SCOPES = ("mine", "shared", "all")


class PollFilter(filters.FilterSet):
    q = TextSearchFilter(fields=("title", "description"))
    scope = filters.ChoiceFilter(
        choices=[(scope, scope) for scope in POLL_SCOPES],
        method="in_scope",
        help_text=(
            "`mine` for the polls the caller created, `shared` for the ones "
            "they were invited to or voted in, `all` (the default) for both."
        ),
    )
    status = MultiValueFilter(child=forms.ChoiceField(choices=Poll.Status.choices))
    ordering = OrderingFilter(
        fields={"created_at": "created_at", "title": "title"},
        tie_break=("uuid",),
    )

    class Meta:
        model = Poll
        fields = []

    def in_scope(self, queryset, name, value):
        own = Q(created_by=self.request.user)
        if value == "mine":
            return queryset.filter(own)
        if value == "shared":
            return queryset.exclude(own)
        return queryset
