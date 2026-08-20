from django.contrib import admin, messages
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display

from .models import (
    Calendar,
    CalendarSubscription,
    Event,
    EventMember,
    Poll,
    PollInvitee,
    PollSlot,
    PollVote,
)
from .models_external import ExternalCalendar
from .services.ics_sync import clear_sync_errors, queue_external_calendar_syncs


class ExternalCalendarInline(StackedInline):
    model = ExternalCalendar
    extra = 0
    readonly_fields = ("last_synced_at", "last_etag", "last_error")


@admin.register(Calendar)
class CalendarAdmin(ModelAdmin):
    list_display = ("name", "owner", "color", "created_at")
    list_select_related = ("owner",)
    search_fields = ("name", "owner__username")
    autocomplete_fields = ("owner",)
    inlines = [ExternalCalendarInline]


class EventMemberInline(TabularInline):
    model = EventMember
    extra = 0


@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = (
        "title",
        "calendar",
        "owner",
        "start",
        "end",
        "all_day",
        "recurrence_frequency",
        "recurrence_parent",
        "is_cancelled",
        "created_at",
    )
    list_filter = ("all_day", "calendar", "recurrence_frequency", "is_cancelled")
    search_fields = ("uuid", "title", "description")
    autocomplete_fields = ("recurrence_parent",)
    # list_display renders calendar/owner/recurrence_parent on every row;
    # without list_select_related the admin changelist issues 3 queries per
    # row (N+1 on the FKs).
    list_select_related = ("calendar", "owner", "recurrence_parent")
    date_hierarchy = "start"
    inlines = [EventMemberInline]


@admin.register(EventMember)
class EventMemberAdmin(ModelAdmin):
    list_display = ("event", "user", "status", "created_at")
    list_filter = ("status",)
    list_select_related = ("event", "user")
    search_fields = ("event__title", "user__username")
    autocomplete_fields = ("event", "user")


@admin.register(CalendarSubscription)
class CalendarSubscriptionAdmin(ModelAdmin):
    list_display = ("user", "calendar", "created_at")
    list_select_related = ("user", "calendar")
    search_fields = ("user__username", "calendar__name")
    autocomplete_fields = ("user", "calendar")


class PollSlotInline(TabularInline):
    model = PollSlot
    extra = 0


class PollVoteInline(TabularInline):
    model = PollVote
    extra = 0
    raw_id_fields = ["user"]


class PollInviteeInline(TabularInline):
    model = PollInvitee
    extra = 0
    raw_id_fields = ["user"]


@admin.register(Poll)
class PollAdmin(ModelAdmin):
    list_display = ["title", "created_by", "status_badge", "created_at"]
    list_filter = ["status"]
    list_select_related = ["created_by"]
    search_fields = ["title"]
    autocomplete_fields = ["created_by", "event"]
    raw_id_fields = ["chosen_slot"]
    inlines = [PollSlotInline, PollInviteeInline]

    @display(
        description="Status",
        label={
            Poll.Status.OPEN: "success",
        },
    )
    def status_badge(self, obj):
        return obj.status


@admin.register(PollVote)
class PollVoteAdmin(ModelAdmin):
    list_display = ["slot", "user", "guest_name", "choice", "created_at"]
    list_filter = ["choice"]
    list_select_related = ["slot", "user"]
    autocomplete_fields = ["user"]
    raw_id_fields = ["slot"]


@admin.register(PollInvitee)
class PollInviteeAdmin(ModelAdmin):
    list_display = ["poll", "user", "created_at"]
    list_select_related = ["poll", "user"]
    autocomplete_fields = ["user", "poll"]


class SyncHealthFilter(admin.SimpleListFilter):
    """Partition feeds the way the ``sync_health`` column colors them."""

    title = "sync health"
    parameter_name = "sync"

    def lookups(self, request, model_admin):
        return [("error", "Error"), ("ok", "OK"), ("never", "Never synced")]

    def queryset(self, request, queryset):
        if self.value() == "error":
            return queryset.exclude(last_error="")
        if self.value() == "ok":
            return queryset.filter(last_error="", last_synced_at__isnull=False)
        if self.value() == "never":
            return queryset.filter(last_error="", last_synced_at__isnull=True)
        return queryset


@admin.register(ExternalCalendar)
class ExternalCalendarAdmin(ModelAdmin):
    list_display = (
        "calendar",
        "url",
        "is_active",
        "sync_health",
        "last_synced_at",
        "last_error",
    )
    list_filter = ("is_active", SyncHealthFilter)
    list_select_related = ("calendar",)
    search_fields = ("calendar__name", "url")
    readonly_fields = ("last_synced_at", "last_etag", "last_error")
    actions = ("sync_now", "clear_error")

    @display(
        description="Sync",
        label={"error": "danger", "ok": "success", "never": "info"},
    )
    def sync_health(self, obj):
        if obj.last_error:
            return "error"
        if obj.last_synced_at is None:
            return "never"
        return "ok"

    @admin.action(description="Sync now")
    def sync_now(self, request, queryset):
        count = queue_external_calendar_syncs(queryset)
        self.message_user(
            request,
            f"Sync queued for {count} active external calendar(s).",
            messages.SUCCESS,
        )

    @admin.action(description="Clear last error")
    def clear_error(self, request, queryset):
        count = clear_sync_errors(queryset)
        self.message_user(
            request, f"Error cleared on {count} row(s).", messages.SUCCESS
        )
