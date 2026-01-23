from django.urls import path, include
from rest_framework.routers import SimpleRouter
from .views import FileViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r'files', FileViewSet, basename='file')

urlpatterns = [
    path('', include(router.urls)),
]
