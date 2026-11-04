"""Every Photos route refuses an anonymous visitor, except the public album
links, which are reached through their token only.

The routes are discovered from the URLconf rather than listed, so a view added
without its login check fails here without anyone remembering to add it. The
identifiers in the URLs name no row: a view that forgot its check answers 404
or 200 for them, and both fail - only a refusal that happens before any lookup
passes.
"""

import uuid

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils.http import url_has_allowed_host_and_scheme

METHODS = ("get", "post", "put", "patch", "delete")

# Reached without an account by design: an album link is its own credential.
# They are checked below to answer an unknown token as a missing page.
PUBLIC_ROUTES = {
    "photos_ui:shared_album",
    "photos_ui:shared_album_timeline",
    "photo-album-link-verify",
    "photo-album-link-archive",
    "photo-album-link-thumbnail",
    "photo-album-link-content",
    "photo-album-link-download",
}


def _photos_routes():
    found = []

    def walk(patterns, namespaces):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(
                    pattern.url_patterns,
                    namespaces + [pattern.namespace]
                    if pattern.namespace
                    else namespaces,
                )
            elif isinstance(pattern, URLPattern) and pattern.name:
                module = getattr(pattern.callback, "__module__", "")
                if module.startswith("workspace.photos."):
                    found.append((":".join([*namespaces, pattern.name]), pattern))

    walk(get_resolver().url_patterns, [])
    return found


def _url(name, pattern):
    regex = pattern.pattern.regex
    kwargs = {
        key: "nope" if key == "token" else str(uuid.uuid4()) for key in regex.groupindex
    }
    return reverse(name, kwargs=kwargs)


def _is_login_redirect(response):
    if response.status_code != 302:
        return False
    location = response.headers["Location"]
    return "/login" in location and url_has_allowed_host_and_scheme(
        location, allowed_hosts=None
    )


class AnonymousAccessTests(TestCase):
    def _request(self, method, url):
        if method == "get":
            return self.client.get(url)
        return getattr(self.client, method)(
            url, data="{}", content_type="application/json"
        )

    def test_the_walk_reaches_every_kind_of_photos_route(self):
        # A walk that finds nothing would let every route through.
        names = {name for name, _ in _photos_routes()}
        self.assertGreater(len(names), 50)
        self.assertIn("photos_ui:index", names)
        self.assertIn("photo-album-detail", names)
        self.assertIn("photos-cluster-detail", names)
        self.assertLessEqual(PUBLIC_ROUTES, names)

    def test_every_private_route_refuses_an_anonymous_visitor(self):
        for name, pattern in _photos_routes():
            if name in PUBLIC_ROUTES:
                continue
            url = _url(name, pattern)
            for method in METHODS:
                with self.subTest(route=name, method=method):
                    response = self._request(method, url)
                    self.assertTrue(
                        response.status_code in (401, 403, 405)
                        or _is_login_redirect(response),
                        f"{method.upper()} {url} answered {response.status_code}",
                    )

    def test_a_public_route_answers_an_unknown_token_as_missing(self):
        for name, pattern in _photos_routes():
            if name not in PUBLIC_ROUTES:
                continue
            url = _url(name, pattern)
            for method in METHODS:
                with self.subTest(route=name, method=method):
                    response = self._request(method, url)
                    self.assertIn(response.status_code, (403, 404, 405))
