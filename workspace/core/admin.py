from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import EmailDelivery, EmailSuppression


@admin.register(EmailDelivery)
class EmailDeliveryAdmin(ModelAdmin):
    """The send record is written by the service alone: editing a row would
    rewrite the answer to "why did this person get this mail"."""

    list_display = (
        "to_address",
        "feature",
        "subject",
        "status",
        "attempts",
        "created_at",
    )
    list_filter = ("status", "feature", "transactional")
    list_select_related = ("user",)
    search_fields = ("to_address", "subject", "message_id", "user__username")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(EmailSuppression)
class EmailSuppressionAdmin(ModelAdmin):
    """Deleting a row lets the instance mail the address again; adding one
    with no feature stops every mail to it."""

    list_display = ("address", "feature", "reason", "created_at")
    list_filter = ("reason",)
    search_fields = ("address", "detail")
    readonly_fields = ("created_at",)
