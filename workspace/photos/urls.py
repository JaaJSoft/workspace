from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import actions, albums, faces

router = SimpleRouter(trailing_slash=False)
router.register(
    r"api/v1/photos/clusters", faces.FaceClusterViewSet, basename="photos-cluster"
)
router.register(r"api/v1/photos/faces", faces.FaceViewSet, basename="photos-face")

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
    path(
        "api/v1/photos/persons",
        faces.FacePersonsView.as_view(),
        name="photos-face-persons",
    ),
    path(
        "api/v1/photos/faces/status",
        faces.FaceStatusView.as_view(),
        name="photos-face-status",
    ),
    path(
        "api/v1/photos/faces/<uuid:pk>/crop",
        faces.FaceCropView.as_view(),
        name="photos-face-crop",
    ),
    path(
        "api/v1/photos/files/<uuid:file_uuid>/faces",
        faces.PhotoFacesView.as_view(),
        name="photos-file-faces",
    ),
    *router.urls,
]
