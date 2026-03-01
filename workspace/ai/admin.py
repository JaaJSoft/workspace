from django.contrib import admin

from .models import AITask, BotProfile


@admin.register(BotProfile)
class BotProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'model', 'created_by', 'created_at']
    list_filter = ['model']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'description']
    raw_id_fields = ['user', 'created_by']
    readonly_fields = ['created_at']


@admin.register(AITask)
class AITaskAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'task_type', 'status', 'owner', 'model_used', 'prompt_tokens', 'completion_tokens', 'created_at', 'completed_at']
    list_filter = ['task_type', 'status', 'model_used']
    search_fields = ['uuid', 'owner__username', 'result', 'error']
    raw_id_fields = ['owner', 'chat_message']
    readonly_fields = ['uuid', 'created_at']
    date_hierarchy = 'created_at'
