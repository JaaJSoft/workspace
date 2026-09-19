from django.urls import path

from . import views

app_name = "people_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/<uuid:uuid>/panel", views.person_panel, name="person_panel"),
]
