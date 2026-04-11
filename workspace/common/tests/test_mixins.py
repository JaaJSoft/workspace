from django.test import SimpleTestCase
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

factory = APIRequestFactory()


class _OpenView(CacheControlMixin, APIView):
    """Base test view with no auth so finalize_response can run end-to-end."""

    permission_classes = [AllowAny]
    authentication_classes: list = []


class DefaultView(_OpenView):
    def get(self, request):
        return Response({'ok': True})


class PublicRevalidateView(_OpenView):
    cache_private = False

    def get(self, request):
        return Response({'ok': True})


class PrivateMaxAgeView(_OpenView):
    cache_max_age = 120

    def get(self, request):
        return Response({'ok': True})


class PublicMaxAgeView(_OpenView):
    cache_max_age = 60
    cache_private = False

    def get(self, request):
        return Response({'ok': True})


class PresetCacheControlView(_OpenView):
    cache_max_age = 300

    def get(self, request):
        response = Response({'ok': True})
        response['Cache-Control'] = 'no-store'
        return response


class ErrorView(_OpenView):
    cache_max_age = 300

    def get(self, request):
        return Response({'detail': 'nope'}, status=404)


class CacheControlMixinTests(SimpleTestCase):
    def _get(self, view_cls):
        request = factory.get('/whatever')
        view = view_cls.as_view()
        return view(request)

    def test_default_is_private_and_must_revalidate(self):
        response = self._get(DefaultView)
        self.assertEqual(
            response['Cache-Control'],
            'private, max-age=0, must-revalidate',
        )

    def test_public_revalidate_when_not_private(self):
        response = self._get(PublicRevalidateView)
        self.assertEqual(
            response['Cache-Control'],
            'public, max-age=0, must-revalidate',
        )

    def test_private_max_age(self):
        response = self._get(PrivateMaxAgeView)
        self.assertEqual(response['Cache-Control'], 'private, max-age=120')

    def test_public_max_age(self):
        response = self._get(PublicMaxAgeView)
        self.assertEqual(response['Cache-Control'], 'public, max-age=60')

    def test_does_not_override_existing_header(self):
        response = self._get(PresetCacheControlView)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_skips_error_responses(self):
        response = self._get(ErrorView)
        self.assertNotIn('Cache-Control', response)
