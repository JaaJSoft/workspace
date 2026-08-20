from django.contrib import admin
from unfold.admin import ModelAdmin, StackedInline, TabularInline

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


class ExternalCalendarInline(StackedInline):
    model = ExternalCalendar
    extra = 0
    readonly_fields = ("last_synced_at", "last_etag", "last_error")


@admin.register(Calendar)
class CalendarAdmin(ModelAdmin):
    list_display = ("name", "owner", "color", "created_at")
    search_fields = ("name",)
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
    search_fields = ("title", "description")
    raw_id_fields = ("recurrence_parent",)
    # list_display renders calendar/owner/recurrence_parent on every row;
    # without list_select_related the admin changelist issues 3 queries per
    # row (N+1 on the FKs).
    list_select_related = ("calendar", "owner", "recurrence_parent")
    inlines = [EventMemberInline]


@admin.register(EventMember)
class EventMemberAdmin(ModelAdmin):
    list_display = ("event", "user", "status", "created_at")
    list_filter = ("status",)


@admin.register(CalendarSubscription)
class CalendarSubscriptionAdmin(ModelAdmin):
    list_display = ("user", "calendar", "created_at")


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
    list_display = ["title", "created_by", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["title"]
    raw_id_fields = ["created_by", "chosen_slot", "event"]
    inlines = [PollSlotInline, PollInviteeInline]


@admin.register(PollVote)
class PollVoteAdmin(ModelAdmin):
    list_display = ["slot", "user", "guest_name", "choice", "created_at"]
    list_filter = ["choice"]
    raw_id_fields = ["user", "slot"]


@admin.register(PollInvitee)
class PollInviteeAdmin(ModelAdmin):
    list_display = ["poll", "user", "created_at"]
    raw_id_fields = ["user", "poll"]


@admin.register(ExternalCalendar)
class ExternalCalendarAdmin(ModelAdmin):
    list_display = ("calendar", "url", "is_active", "last_synced_at", "last_error")
    list_filter = ("is_active",)
    search_fields = ("calendar__name", "url")
    readonly_fields = ("last_synced_at", "last_etag", "last_error")
