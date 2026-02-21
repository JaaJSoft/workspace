from django.contrib import admin

from .models import Calendar, CalendarSubscription, Event, EventMember


@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'color', 'created_at')
    search_fields = ('name',)


class EventMemberInline(admin.TabularInline):
    model = EventMember
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'calendar', 'owner', 'start', 'end', 'all_day', 'recurrence_frequency', 'recurrence_parent', 'is_cancelled', 'created_at')
    list_filter = ('all_day', 'calendar', 'recurrence_frequency', 'is_cancelled')
    search_fields = ('title', 'description')
    raw_id_fields = ('recurrence_parent',)
    inlines = [EventMemberInline]


@admin.register(EventMember)
class EventMemberAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(CalendarSubscription)
class CalendarSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'calendar', 'created_at')


from .models import Poll, PollSlot, PollVote


class PollSlotInline(admin.TabularInline):
    model = PollSlot
    extra = 0


class PollVoteInline(admin.TabularInline):
    model = PollVote
    extra = 0
    raw_id_fields = ['user']


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_by', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['title']
    raw_id_fields = ['created_by', 'chosen_slot', 'event']
    inlines = [PollSlotInline]


@admin.register(PollVote)
class PollVoteAdmin(admin.ModelAdmin):
    list_display = ['slot', 'user', 'guest_name', 'choice', 'created_at']
    list_filter = ['choice']
    raw_id_fields = ['user', 'slot']
