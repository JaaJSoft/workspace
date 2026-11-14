from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.views import View

from workspace.common.middleware import PrefetchGateMiddleware
from workspace.common.prefetch import (
    is_prefetchable,
    prefetchable,
    speculative_purpose,
)


def plain(request, **kwargs):
    return HttpResponse("plain")


@prefetchable
def opted_in(request, **kwargs):
    return HttpResponse("opted in")


@prefetchable
class OptedInView(View):
    def get(self, request):
        return HttpResponse("class")


class SpeculativePurposeTests(SimpleTestCase):
    def _purpose(self, **headers):
        return speculative_purpose(RequestFactory().get("/", headers=headers))

    def test_a_normal_request_is_not_speculative(self):
        self.assertIsNone(self._purpose())

    def test_prefetch_and_its_parameters(self):
        self.assertEqual(self._purpose(**{"Sec-Purpose": "prefetch"}), set())
        self.assertEqual(
            self._purpose(**{"Sec-Purpose": "prefetch;prerender"}), {"prerender"}
        )
        self.assertEqual(self._purpose(Purpose="prefetch"), set())

    def test_another_purpose_is_not_a_prefetch(self):
        self.assertIsNone(self._purpose(**{"Sec-Purpose": "prerender-ish"}))


class IsPrefetchableTests(SimpleTestCase):
    def test_function_and_class_views(self):
        self.assertTrue(is_prefetchable(opted_in))
        self.assertTrue(is_prefetchable(OptedInView.as_view()))
        self.assertFalse(is_prefetchable(plain))

    def test_survives_a_wrapping_decorator(self):
        self.assertTrue(is_prefetchable(login_required(opted_in)))
        self.assertTrue(is_prefetchable(prefetchable(login_required(plain))))


class PrefetchGateMiddlewareTests(SimpleTestCase):
    def _call(self, view, **headers):
        request = RequestFactory().get("/", headers=headers)
        middleware = PrefetchGateMiddleware(lambda request: view(request))
        refusal = middleware.process_view(request, view, (), {})
        return refusal or middleware(request)

    def test_a_normal_request_reaches_any_view(self):
        self.assertEqual(self._call(plain).content, b"plain")

    def test_a_prefetch_of_a_view_that_did_not_opt_in_is_refused(self):
        response = self._call(plain, **{"Sec-Purpose": "prefetch"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_prefetch_of_an_opted_in_view_is_served(self):
        response = self._call(opted_in, **{"Sec-Purpose": "prefetch"})
        self.assertEqual(response.content, b"opted in")
        response = self._call(OptedInView.as_view(), **{"Sec-Purpose": "prefetch"})
        self.assertEqual(response.content, b"class")

    def test_a_prerender_is_always_refused(self):
        response = self._call(opted_in, **{"Sec-Purpose": "prefetch;prerender"})
        self.assertEqual(response.status_code, 403)
