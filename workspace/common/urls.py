from django.urls import path

from workspace.common.views import ModulesView

urlpatterns = [
    path('api/v1/modules', ModulesView.as_view(), name='modules-list'),
]
