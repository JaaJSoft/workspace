from django.test import RequestFactory, TestCase
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin


class _NoStoreView(CacheControlMixin, APIView):
    authentication_classes = []
    permission_classes = []
    cache_no_store = True

    def get(self, request):
        return Response({"ok": True})


class _DefaultView(CacheControlMixin, APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"ok": True})


class _CachingNoStoreView(_NoStoreView):
    cache_max_age = 300
    cache_stale_while_revalidate = 60


class _FailingNoStoreView(_NoStoreView):
    def get(self, request):
        return Response({"detail": "nope"}, status=404)


class _PreSetHeaderNoStoreView(_NoStoreView):
    def get(self, request):
        response = Response({"ok": True})
        response["Cache-Control"] = "public, max-age=3600"
        return response


class _PreSetHeaderDefaultView(_DefaultView):
    def get(self, request):
        response = Response({"ok": True})
        response["Cache-Control"] = "public, max-age=3600"
        return response


class CacheControlNoStoreTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _header(self, view):
        return view.as_view()(self.factory.get("/x")).get("Cache-Control")

    def test_no_store_replaces_the_revalidate_directive(self):
        self.assertEqual(self._header(_NoStoreView), "no-store")

    def test_the_default_is_unchanged(self):
        self.assertEqual(
            self._header(_DefaultView), "private, max-age=0, must-revalidate"
        )

    def test_no_store_wins_over_a_configured_max_age(self):
        """A view that declares both is contradicting itself, and the safe
        reading of the contradiction is the one that stores nothing."""
        self.assertEqual(self._header(_CachingNoStoreView), "no-store")

    def test_no_store_overrides_a_header_the_response_already_carries(self):
        """The mixin otherwise defers to whoever set the header first, which
        is right for a caching policy and wrong for a refusal to cache."""
        self.assertEqual(self._header(_PreSetHeaderNoStoreView), "no-store")

    def test_an_existing_header_still_wins_without_no_store(self):
        self.assertEqual(self._header(_PreSetHeaderDefaultView), "public, max-age=3600")

    def test_an_error_response_is_left_alone(self):
        self.assertIsNone(self._header(_FailingNoStoreView))
