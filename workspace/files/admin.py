from django.contrib import admin

from .models import MimeTypeRule


@admin.register(MimeTypeRule)
class MimeTypeRuleAdmin(admin.ModelAdmin):
    list_display = ('pattern', 'priority', 'icon', 'color', 'category', 'viewer_type', 'is_wildcard')
    list_editable = ('priority', 'icon', 'color', 'category', 'viewer_type')
    list_filter = ('category', 'is_wildcard')
    search_fields = ('pattern',)
    ordering = ('priority', 'pattern')
