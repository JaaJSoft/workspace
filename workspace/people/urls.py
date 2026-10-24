from django.urls import include, path, re_path
from rest_framework.routers import SimpleRouter

from .views import actions, avatar, carddav, lists, persons, promote, vcard

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
        "api/v1/people/promote",
        promote.PersonPromoteView.as_view(),
        name="person-promote",
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
        "api/v1/people/qrcode",
        vcard.PersonQRCodeView.as_view(),
        name="person-qrcode",
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
    # CardDAV. Collections answer with or without their trailing slash: the
    # hrefs we serve carry it, but clients are not consistent about keeping it.
    re_path(r"^\.well-known/carddav/?$", carddav.well_known, name="carddav-well-known"),
    re_path(r"^carddav/?$", carddav.CardDavRootView.as_view(), name="carddav-root"),
    re_path(
        r"^carddav/principals/?$",
        carddav.CardDavRootView.as_view(),
        name="carddav-principals",
    ),
    re_path(
        r"^carddav/principals/(?P<username>[^/]+)/?$",
        carddav.PrincipalView.as_view(),
        name="carddav-principal",
    ),
    re_path(
        r"^carddav/addressbooks/(?P<username>[^/]+)/?$",
        carddav.AddressBookHomeView.as_view(),
        name="carddav-home",
    ),
    re_path(
        r"^carddav/addressbooks/(?P<username>[^/]+)/(?P<slug>[\w-]+)/?$",
        carddav.AddressBookView.as_view(),
        name="carddav-book",
    ),
    re_path(
        r"^carddav/addressbooks/(?P<username>[^/]+)/(?P<slug>[\w-]+)/(?P<name>[^/]+)$",
        carddav.CardView.as_view(),
        name="carddav-card",
    ),
]
