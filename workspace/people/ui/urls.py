from django.urls import path

from . import views

app_name = "people_ui"

urlpatterns = [
    path("", views.index, name="index"),
]
