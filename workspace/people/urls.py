from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import persons

router = SimpleRouter(trailing_slash=False)
router.register(r"people", persons.PersonViewSet, basename="person")

urlpatterns = [
    # Explicit routes go before the router so `lists` and `actions` are
    # never read as a person uuid.
    path("api/v1/", include(router.urls)),
]
