from django.urls import path

from workspace.core.views import ModulesView, UnifiedSearchView

urlpatterns = [
    path('api/v1/modules', ModulesView.as_view(), name='modules-list'),
    path('api/v1/search', UnifiedSearchView.as_view(), name='unified-search'),
]
