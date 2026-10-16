from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views.files import FileViewSet
from .views.graph import FileGraphView
from .views.sensitive import SensitiveFilesView
from .views.share_links import (
    SharedFileContentView,
    SharedFileDownloadView,
    SharedFileMetaView,
    SharedFileThumbnailView,
    SharedFileVerifyView,
    SharedFolderUploadView,
)
from .views.tags import FileTagView, TagViewSet
from .views.thumbnails import GenerateThumbnailsView
from .views.uploads import (
    UploadSessionFinalizeView,
    UploadSessionListView,
    UploadSessionView,
)
from .views.wopi import WopiFileContentsView, WopiFileView

router = SimpleRouter(trailing_slash=False)
router.register(r"files", FileViewSet, basename="file")
router.register(r"tags", TagViewSet, basename="tag")

urlpatterns = [
    path(
        "api/v1/files/sensitive",
        SensitiveFilesView.as_view(),
        name="files-sensitive",
    ),
    # Ahead of the router, whose file detail route would take "uploads" for
    # a file's id.
    path(
        "api/v1/files/uploads",
        UploadSessionListView.as_view(),
        name="file-upload-sessions",
    ),
    path(
        "api/v1/files/uploads/<uuid:uuid>",
        UploadSessionView.as_view(),
        name="file-upload-session",
    ),
    path(
        "api/v1/files/uploads/<uuid:uuid>/finalize",
        UploadSessionFinalizeView.as_view(),
        name="file-upload-session-finalize",
    ),
    path(
        "api/v1/files/shared/<str:token>",
        SharedFileMetaView.as_view(),
        name="shared-file-meta",
    ),
    path(
        "api/v1/files/shared/<str:token>/verify",
        SharedFileVerifyView.as_view(),
        name="shared-file-verify",
    ),
    path(
        "api/v1/files/shared/<str:token>/content",
        SharedFileContentView.as_view(),
        name="shared-file-content",
    ),
    path(
        "api/v1/files/shared/<str:token>/download",
        SharedFileDownloadView.as_view(),
        name="shared-file-download",
    ),
    path(
        "api/v1/files/shared/<str:token>/thumbnail",
        SharedFileThumbnailView.as_view(),
        name="shared-file-thumbnail",
    ),
    path(
        "api/v1/files/shared/<str:token>/upload",
        SharedFolderUploadView.as_view(),
        name="shared-folder-upload",
    ),
    path(
        "api/v1/files/<uuid:file_uuid>/tags", FileTagView.as_view(), name="file-tag-add"
    ),
    path(
        "api/v1/files/<uuid:file_uuid>/tags/<uuid:tag_uuid>",
        FileTagView.as_view(),
        name="file-tag-remove",
    ),
    path("api/v1/files/graph", FileGraphView.as_view(), name="file-graph"),
    # WOPI host endpoints - the path shape is fixed by the protocol: the
    # editor appends /contents to the WOPISrc it was handed.
    path("api/wopi/files/<uuid:uuid>", WopiFileView.as_view(), name="wopi-file"),
    path(
        "api/wopi/files/<uuid:uuid>/contents",
        WopiFileContentsView.as_view(),
        name="wopi-file-contents",
    ),
    path("api/v1/", include(router.urls)),
    path(
        "api/v1/thumbnails/generate",
        GenerateThumbnailsView.as_view(),
        name="generate-thumbnails",
    ),
]
