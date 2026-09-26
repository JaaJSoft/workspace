from django.urls import path

from .views import actions, albums

urlpatterns = [
    path(
        "api/v1/photos/albums",
        albums.AlbumListView.as_view(),
        name="photo-albums",
    ),
    path(
        "api/v1/photos/albums/actions",
        actions.AlbumActionsView.as_view(),
        name="photo-album-actions",
    ),
    path(
        "api/v1/photos/albums/<uuid:uuid>",
        albums.AlbumDetailView.as_view(),
        name="photo-album-detail",
    ),
    path(
        "api/v1/photos/albums/<uuid:uuid>/items",
        albums.AlbumItemsView.as_view(),
        name="photo-album-items",
    ),
    path(
        "api/v1/photos/albums/<uuid:uuid>/items/remove",
        albums.AlbumItemsRemoveView.as_view(),
        name="photo-album-items-remove",
    ),
    path(
        "api/v1/photos/albums/<uuid:uuid>/reorder",
        albums.AlbumReorderView.as_view(),
        name="photo-album-reorder",
    ),
]
