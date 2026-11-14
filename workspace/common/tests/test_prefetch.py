import json
import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import include, path, re_path
from django.views import View

from workspace.common.middleware import PrefetchGateMiddleware
from workspace.common.prefetch import (
    is_prefetchable,
    prefetchable,
    prefetchable_paths,
    prefetchable_routes,
    speculation_rules,
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


module_patterns = (
    [
        path("", opted_in, name="index"),
        path("/<uuid:pk>/card", opted_in, name="card"),
        path("/plain", plain, name="plain"),
    ],
    "module",
)

urlpatterns = [
    path("", opted_in, name="home"),
    path("module", include(module_patterns)),
    path("space/<int:pk>/", include([path("inside", opted_in, name="inside")])),
    path("class", OptedInView.as_view(), name="class"),
    path("unnamed", opted_in),
    re_path(r"^legacy/(\d+)$", opted_in, name="legacy"),
    path("plain", plain, name="plain"),
]

URLCONF = "workspace.common.tests.test_prefetch"


@override_settings(ROOT_URLCONF=URLCONF)
class SpeculationRulesTests(SimpleTestCase):
    def test_routes_of_marked_views_with_their_parameters(self):
        self.assertEqual(
            sorted(prefetchable_routes()),
            [
                ("class", False),
                ("home", False),
                ("inside", True),
                ("legacy", True),
                ("module:card", True),
                ("module:index", False),
            ],
        )

    def test_rules_name_the_paths_of_routes_without_a_parameter(self):
        self.assertEqual(prefetchable_paths(), ("/", "/class", "/module"))
        rule = speculation_rules()["prefetch"][0]
        self.assertEqual(rule["eagerness"], "moderate")
        conditions = rule["where"]["and"]
        self.assertEqual(
            conditions[0]["href_matches"],
            [{"pathname": "/"}, {"pathname": "/class"}, {"pathname": "/module"}],
        )
        self.assertIn("[x-target\\.push]", conditions[1]["not"]["selector_matches"])

    def test_template_tag_renders_the_rules(self):
        html = Template("{% load ui_tags %}{% speculation_rules %}").render(Context())
        match = re.fullmatch(r'<script type="speculationrules">(.*)</script>', html)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match[1]), speculation_rules())


@override_settings(ROOT_URLCONF="workspace.common.tests.test_prefetch_plain_urls")
class NoMarkedViewTests(SimpleTestCase):
    def test_nothing_is_rendered(self):
        self.assertIsNone(speculation_rules())
        html = Template("{% load ui_tags %}{% speculation_rules %}").render(Context())
        self.assertEqual(html, "")


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
