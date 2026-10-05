from django.urls import path

from . import views

app_name = "photos_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/timeline", views.timeline, name="timeline"),
    path("/albums/<uuid:uuid>", views.album, name="album"),
    path("/albums/<uuid:uuid>/timeline", views.album_timeline, name="album_timeline"),
    path(
        "/albums/<uuid:uuid>/view/<uuid:file_uuid>",
        views.album_viewer,
        name="album_viewer",
    ),
    path("/shared/<str:token>", views.shared_album, name="shared_album"),
    path(
        "/shared/<str:token>/timeline",
        views.shared_album_timeline,
        name="shared_album_timeline",
    ),
    path("/people", views.people, name="people"),
    path("/people/review", views.people_review, name="people_review"),
    path("/people/faces", views.person_faces_view, name="person_faces"),
]
