from django.urls import path

from workspace.core.activity_views import (
    ActivityDailyCountsView,
    ActivityRecentView,
    ActivityStatsView,
)
from workspace.core.views import ModulesView, UnifiedSearchView
from workspace.core.views_changelog import changelog_partial
from workspace.core.views_sse import global_stream

urlpatterns = [
    path('api/v1/modules', ModulesView.as_view(), name='modules-list'),
    path('api/v1/search', UnifiedSearchView.as_view(), name='unified-search'),
    path('api/v1/stream', global_stream, name='global-sse-stream'),
    path('api/v1/activity/recents', ActivityRecentView.as_view(), name='activity-recent'),
    path('api/v1/activity/daily-counts', ActivityDailyCountsView.as_view(), name='activity-daily-counts'),
    path('api/v1/activity/stats', ActivityStatsView.as_view(), name='activity-stats'),
    path('changelog', changelog_partial, name='changelog-partial'),
]
