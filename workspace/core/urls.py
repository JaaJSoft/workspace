from django.urls import path

from workspace.core.views import ModulesView, UnifiedSearchView
from workspace.core.views_sse import global_stream

urlpatterns = [
    path('api/v1/modules', ModulesView.as_view(), name='modules-list'),
    path('api/v1/search', UnifiedSearchView.as_view(), name='unified-search'),
    path('api/v1/stream', global_stream, name='global-sse-stream'),
]
