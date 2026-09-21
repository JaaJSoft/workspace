from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import actions, avatar, lists, persons, vcard

router = SimpleRouter(trailing_slash=False)
router.register(r"people", persons.PersonViewSet, basename="person")

urlpatterns = [
    # Explicit routes go before the router so `lists` and `actions` are
    # never read as a person uuid.
    path("api/v1/people/lists", lists.PersonListView.as_view(), name="person-lists"),
    path(
        "api/v1/people/actions",
        actions.PersonActionsView.as_view(),
        name="person-actions",
    ),
    path(
        "api/v1/people/import",
        vcard.PersonImportView.as_view(),
        name="person-import",
    ),
    path(
        "api/v1/people/export",
        vcard.PersonExportView.as_view(),
        name="person-export",
    ),
    path(
        "api/v1/people/lists/<uuid:uuid>/vcf",
        vcard.PersonListVCardView.as_view(),
        name="person-list-vcf",
    ),
    path(
        "api/v1/people/<uuid:uuid>/vcf",
        vcard.PersonVCardView.as_view(),
        name="person-vcf",
    ),
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
    path(
        "api/v1/people/<uuid:uuid>/avatar",
        avatar.PersonAvatarView.as_view(),
        name="person-avatar",
    ),
    path("api/v1/", include(router.urls)),
]
