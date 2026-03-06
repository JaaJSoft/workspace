from django import forms
from django.contrib import admin
from django.utils.html import format_html

from workspace.users.avatar_service import (
    delete_avatar,
    get_avatar_path,
    has_avatar,
)

from .models import AITask, BotProfile


class BotProfileForm(forms.ModelForm):
    avatar = forms.ImageField(
        required=False,
        help_text='Upload a new avatar image. Will be cropped to a centered square and saved as 256×256 WebP.',
    )
    delete_avatar = forms.BooleanField(
        required=False,
        help_text='Check to remove the current avatar.',
    )

    class Meta:
        model = BotProfile
        fields = '__all__'


@admin.register(BotProfile)
class BotProfileAdmin(admin.ModelAdmin):
    form = BotProfileForm
    list_display = ['user', 'model', 'is_public', 'supports_tools', 'supports_vision', 'created_by', 'created_at']
    list_filter = ['model', 'is_public', 'supports_tools', 'supports_vision']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'description']
    raw_id_fields = ['user', 'created_by']
    readonly_fields = ['created_at', 'avatar_preview']
    filter_horizontal = ['allowed_users', 'allowed_groups']

    def avatar_preview(self, obj):
        if not obj.pk or not has_avatar(obj.user):
            return 'No avatar'
        path = get_avatar_path(obj.user.id)
        from django.core.files.storage import default_storage
        url = default_storage.url(path)
        return format_html(
            '<img src="{}" style="width:96px;height:96px;border-radius:50%;object-fit:cover;" />',
            url,
        )
    avatar_preview.short_description = 'Current avatar'

    def get_fieldsets(self, request, obj=None):
        return [
            (None, {'fields': ['user', 'system_prompt', 'model', 'description']}),
            ('Avatar', {'fields': ['avatar_preview', 'avatar', 'delete_avatar']}),
            ('Capabilities', {'fields': ['supports_tools', 'supports_vision']}),
            ('Access control', {'fields': ['is_public', 'created_by', 'allowed_users', 'allowed_groups']}),
            ('Info', {'fields': ['created_at']}),
        ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get('delete_avatar'):
            delete_avatar(obj.user)
        elif form.cleaned_data.get('avatar'):
            self._save_avatar(obj.user, form.cleaned_data['avatar'])

    @staticmethod
    def _save_avatar(user, image_file):
        from PIL import Image, ImageOps
        from workspace.common.image_service import save_image
        from workspace.users.settings_service import set_setting
        from io import BytesIO

        img = Image.open(image_file)
        img = ImageOps.exif_transpose(img)

        # Auto center-crop to square
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

        img = img.convert('RGB')
        img = img.resize((256, 256), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, format='WEBP', quality=85)

        path = get_avatar_path(user.id)
        save_image(path, buf.getvalue())
        set_setting(user, 'profile', 'has_avatar', True)


@admin.register(AITask)
class AITaskAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'task_type', 'status', 'owner', 'model_used', 'prompt_tokens', 'completion_tokens', 'created_at', 'completed_at']
    list_filter = ['task_type', 'status', 'model_used']
    search_fields = ['uuid', 'owner__username', 'result', 'error']
    raw_id_fields = ['owner', 'chat_message']
    readonly_fields = ['uuid', 'created_at']
    date_hierarchy = 'created_at'
