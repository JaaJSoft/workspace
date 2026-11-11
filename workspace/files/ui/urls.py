from django.urls import path

from . import views

app_name = "files_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/trash", views.trash, name="trash"),
    path("/share-target", views.share_target, name="share_target"),
    path("/pinned", views.pinned_folders, name="pinned_folders"),
    path("/group-folders", views.group_folders_sidebar, name="group_folders_sidebar"),
    path("/properties/<uuid:uuid>", views.properties, name="properties"),
    path("/storage", views.storage, name="storage"),
    path("/storage/<uuid:uuid>", views.storage, name="storage_folder"),
    path("/<uuid:uuid>/events", views.events, name="events"),
    path("/<uuid:uuid>/card", views.file_card, name="file_card"),
    path("/previews", views.file_previews, name="file_previews"),
    path("/view/<uuid:uuid>", views.view_file, name="view_file"),
    path("/reader", views.ebook_reader, name="ebook_reader"),
    path("/shared/<str:token>", views.shared_file_view, name="shared_file"),
    path("/<uuid:folder>", views.index, name="folder"),
]
