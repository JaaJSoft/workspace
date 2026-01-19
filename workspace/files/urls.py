from django.urls import path, include
from rest_framework.routers import DefaultRouter, SimpleRouter
from .views import FileNodeViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r'nodes', FileNodeViewSet, basename='filenode')

urlpatterns = [
    path('', include(router.urls)),
]
