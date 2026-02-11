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
