from django.urls import path, include
from rest_framework.routers import SimpleRouter
from .views import FileViewSet
from .views_thumbnails import GenerateThumbnailsView

router = SimpleRouter(trailing_slash=False)
router.register(r'files', FileViewSet, basename='file')

urlpatterns = [
    path('api/v1/', include(router.urls)),
    path('api/v1/thumbnails/generate', GenerateThumbnailsView.as_view(), name='generate-thumbnails'),
]
