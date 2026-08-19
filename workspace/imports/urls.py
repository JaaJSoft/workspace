from django.urls import path

from . import views

urlpatterns = [
    path(
        "api/v1/imports/providers",
        views.ProviderListView.as_view(),
        name="imports-providers",
    ),
    path(
        "api/v1/imports/connections",
        views.ConnectionListView.as_view(),
        name="imports-connections",
    ),
    path(
        "api/v1/imports/connections/<uuid:uuid>",
        views.ConnectionDetailView.as_view(),
        name="imports-connection-detail",
    ),
    path(
        "api/v1/imports/connections/<uuid:uuid>/test",
        views.ConnectionTestView.as_view(),
        name="imports-connection-test",
    ),
    path(
        "api/v1/imports/connections/<uuid:uuid>/browse",
        views.ConnectionBrowseView.as_view(),
        name="imports-connection-browse",
    ),
]
