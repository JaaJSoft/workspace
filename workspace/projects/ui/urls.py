from django.urls import path

from . import views

app_name = "projects_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/<uuid:project_uuid>", views.overview, name="project"),
    path("/<uuid:project_uuid>/board", views.board, name="board"),
    path("/<uuid:project_uuid>/backlog", views.backlog, name="backlog"),
    path("/<uuid:project_uuid>/tasks", views.all_tasks, name="all_tasks"),
    path("/<uuid:project_uuid>/analytics", views.analytics, name="analytics"),
    path("/<uuid:project_uuid>/settings", views.settings_view, name="settings"),
    path(
        "/<uuid:project_uuid>/tasks/<uuid:task_uuid>/panel",
        views.task_panel,
        name="task_panel",
    ),
    path(
        "/<uuid:project_uuid>/tasks/<uuid:task_uuid>/card",
        views.task_card,
        name="task_card",
    ),
]
