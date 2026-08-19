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
    path("api/v1/imports/jobs", views.JobListView.as_view(), name="imports-jobs"),
    path(
        "api/v1/imports/jobs/<uuid:uuid>",
        views.JobDetailView.as_view(),
        name="imports-job-detail",
    ),
    path(
        "api/v1/imports/jobs/<uuid:uuid>/items",
        views.JobItemsView.as_view(),
        name="imports-job-items",
    ),
    path(
        "api/v1/imports/jobs/<uuid:uuid>/cancel",
        views.JobCancelView.as_view(),
        name="imports-job-cancel",
    ),
    path(
        "api/v1/imports/jobs/<uuid:uuid>/retry",
        views.JobRetryView.as_view(),
        name="imports-job-retry",
    ),
]
