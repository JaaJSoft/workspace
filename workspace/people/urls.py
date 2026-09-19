from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import lists, persons

router = SimpleRouter(trailing_slash=False)
router.register(r"people", persons.PersonViewSet, basename="person")

urlpatterns = [
    # Explicit routes go before the router so `lists` and `actions` are
    # never read as a person uuid.
    path("api/v1/people/lists", lists.PersonListView.as_view(), name="person-lists"),
    path(
        "api/v1/people/lists/<uuid:uuid>",
        lists.PersonListDetailView.as_view(),
        name="person-list-detail",
    ),
    path(
        "api/v1/people/lists/<uuid:uuid>/members",
        lists.PersonListMembersView.as_view(),
        name="person-list-members",
    ),
    path("api/v1/", include(router.urls)),
]
