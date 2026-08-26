from django import forms
from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.decorators import display

from workspace.users.services.avatar import (
    delete_avatar,
    get_avatar_path,
    has_avatar,
    save_avatar_centered,
)

from .models import AITask, BotProfile, ConversationSummary


class BotProfileForm(forms.ModelForm):
    avatar = forms.ImageField(
        required=False,
        help_text="Upload a new avatar image. Will be cropped to a centered square and saved as 256×256 WebP.",
    )
    delete_avatar = forms.BooleanField(
        required=False,
        help_text="Check to remove the current avatar.",
    )

    class Meta:
        model = BotProfile
        # Admin-only form: __all__ is fine, mass-assignment is not a concern.
        fields = "__all__"  # noqa: DJ007


@admin.register(BotProfile)
class BotProfileAdmin(ModelAdmin):
    form = BotProfileForm
    list_display = [
        "user",
        "model",
        "is_public",
        "supports_tools",
        "supports_vision",
        "created_by",
        "created_at",
    ]
    list_filter = ["model", "is_public", "supports_tools", "supports_vision"]
    search_fields = [
        "user__username",
        "user__first_name",
        "user__last_name",
        "description",
    ]
    list_select_related = ["user", "created_by"]
    autocomplete_fields = ["user", "created_by"]
    readonly_fields = ["created_at", "avatar_preview"]
    filter_horizontal = ["allowed_users", "allowed_groups"]

    def avatar_preview(self, obj):
        if not obj.pk or not has_avatar(obj.user):
            return "No avatar"
        path = get_avatar_path(obj.user.id)
        from django.core.files.storage import default_storage

        url = default_storage.url(path)
        return format_html(
            '<img src="{}" style="width:96px;height:96px;border-radius:50%;object-fit:cover;" />',
            url,
        )

    avatar_preview.short_description = "Current avatar"

    def get_fieldsets(self, request, obj=None):
        return [
            (None, {"fields": ["user", "system_prompt", "model", "description"]}),
            ("Avatar", {"fields": ["avatar_preview", "avatar", "delete_avatar"]}),
            ("Capabilities", {"fields": ["supports_tools", "supports_vision"]}),
            (
                "Voice",
                {
                    "fields": ["voice_ref", "voice_ref_text"],
                    "description": (
                        "The recording is the voice, and the only thing that "
                        "decides how the bot sounds. A few seconds of speech, "
                        "produced outside the workspace \u2014 designed by a "
                        "voice-design model, or lifted from any clean sample "
                        "\u2014 and cloned on every voice message, with the "
                        "transcript matching it word for word so the backend "
                        "can align the clone. A bot with no recording here, "
                        "and no AI_TTS_VOICE_REF to fall back on, cannot send "
                        "voice messages at all."
                    ),
                },
            ),
            (
                "Access control",
                {
                    "fields": [
                        "is_public",
                        "created_by",
                        "allowed_users",
                        "allowed_groups",
                    ]
                },
            ),
            ("Info", {"fields": ["created_at"]}),
        ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("delete_avatar"):
            delete_avatar(obj.user)
        elif form.cleaned_data.get("avatar"):
            save_avatar_centered(obj.user, form.cleaned_data["avatar"])


@admin.register(ConversationSummary)
class ConversationSummaryAdmin(ModelAdmin):
    list_display = ("conversation", "up_to", "content_preview", "updated_at")
    list_select_related = ("conversation",)
    search_fields = ("conversation__title", "content")
    readonly_fields = ("conversation", "up_to", "content", "updated_at")

    @admin.display(description="Summary")
    def content_preview(self, obj):
        if not obj.content:
            return "—"
        return obj.content[:120] + "…" if len(obj.content) > 120 else obj.content

    # Summaries are produced and refreshed by the summarizer task; a
    # hand-edited row would desync from `up_to`. Deletion stays open - the
    # next pass rebuilds it.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AITask)
class AITaskAdmin(ModelAdmin):
    list_display = [
        "uuid",
        "task_type",
        "status_badge",
        "owner",
        "model_used",
        "prompt_tokens",
        "completion_tokens",
        "created_at",
        "completed_at",
    ]
    list_filter = [
        "task_type",
        "status",
        "model_used",
        ("created_at", RangeDateTimeFilter),
    ]
    list_filter_submit = True
    list_select_related = ["owner"]
    search_fields = ["uuid", "owner__username", "result", "error"]
    autocomplete_fields = ["owner", "chat_message"]
    readonly_fields = ["uuid", "created_at", "raw_messages"]
    date_hierarchy = "created_at"

    @display(
        description="Status",
        label={
            AITask.Status.PENDING: "info",
            AITask.Status.PROCESSING: "warning",
            AITask.Status.COMPLETED: "success",
            AITask.Status.FAILED: "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status
