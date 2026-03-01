from django.urls import path

from . import views

urlpatterns = [
    path(
        'api/v1/ai/bots',
        views.BotListView.as_view(),
        name='ai-bots',
    ),
    path(
        'api/v1/ai/tasks/summarize',
        views.SummarizeView.as_view(),
        name='ai-summarize',
    ),
    path(
        'api/v1/ai/tasks/compose',
        views.ComposeView.as_view(),
        name='ai-compose',
    ),
    path(
        'api/v1/ai/tasks/reply',
        views.ReplyView.as_view(),
        name='ai-reply',
    ),
    path(
        'api/v1/ai/tasks/<uuid:task_id>',
        views.TaskDetailView.as_view(),
        name='ai-task-detail',
    ),
]
