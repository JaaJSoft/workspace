from django.urls import path, include
from rest_framework.routers import SimpleRouter
from .views import FileViewSet
from .views_tags import TagViewSet, FileTagView
from .views_thumbnails import GenerateThumbnailsView
from .views_share_links import (
    SharedFileMetaView,
    SharedFileVerifyView,
    SharedFileContentView,
    SharedFileDownloadView,
)

router = SimpleRouter(trailing_slash=False)
router.register(r'files', FileViewSet, basename='file')
router.register(r'tags', TagViewSet, basename='tag')

urlpatterns = [
    path('api/v1/files/shared/<str:token>', SharedFileMetaView.as_view(), name='shared-file-meta'),
    path('api/v1/files/shared/<str:token>/verify', SharedFileVerifyView.as_view(), name='shared-file-verify'),
    path('api/v1/files/shared/<str:token>/content', SharedFileContentView.as_view(), name='shared-file-content'),
    path('api/v1/files/shared/<str:token>/download', SharedFileDownloadView.as_view(), name='shared-file-download'),
    path('api/v1/files/<uuid:file_uuid>/tags', FileTagView.as_view(), name='file-tag-add'),
    path('api/v1/files/<uuid:file_uuid>/tags/<uuid:tag_uuid>', FileTagView.as_view(), name='file-tag-remove'),
    path('api/v1/', include(router.urls)),
    path('api/v1/thumbnails/generate', GenerateThumbnailsView.as_view(), name='generate-thumbnails'),
]
