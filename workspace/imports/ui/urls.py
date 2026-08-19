from django.urls import path

from . import views

app_name = "imports_ui"

urlpatterns = [
    path("", views.index, name="index"),
]
