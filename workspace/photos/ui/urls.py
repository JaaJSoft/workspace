from django.urls import path

from . import views

app_name = "photos_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/timeline", views.timeline, name="timeline"),
    path("/albums/<uuid:uuid>", views.album, name="album"),
    path("/albums/<uuid:uuid>/timeline", views.album_timeline, name="album_timeline"),
]
