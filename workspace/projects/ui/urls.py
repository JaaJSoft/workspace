from django.urls import path

from . import views

app_name = "projects_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/<uuid:project_uuid>", views.overview, name="project"),
    path("/<uuid:project_uuid>/board", views.board, name="board"),
    path("/<uuid:project_uuid>/backlog", views.backlog, name="backlog"),
]
