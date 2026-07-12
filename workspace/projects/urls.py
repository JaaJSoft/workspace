from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .viewsets import ProjectViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r"projects", ProjectViewSet, basename="project")

urlpatterns = [
    path("api/v1/", include(router.urls)),
]
