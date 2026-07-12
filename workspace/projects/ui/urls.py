from django.urls import path

from . import views

app_name = "projects_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/<uuid:project_uuid>", views.project_view, name="project"),
]
