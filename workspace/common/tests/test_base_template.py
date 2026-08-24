"""Regression tests for ``base.html`` / ``base_with_navbar.html``."""

import re
from pathlib import Path

from django.template import Context, Template
from django.template.loader import get_template
from django.test import TestCase

STYLESHEET = (
    Path(__file__).resolve().parents[2] / "common" / "static" / "css" / "app.css"
)


def _stylesheet_without_whitespace() -> str:
    """The built stylesheet, with whitespace squeezed out: an assertion on it
    must not depend on where the minifier chose to break a line."""
    return re.sub(r"\s+", "", STYLESHEET.read_text(encoding="utf-8"))


class NavbarLayoutScrollLockTests(TestCase):
    """DaisyUI emits ``html:has(.drawer-open.drawer-open) { overflow-y: auto }``
    which lets the html element scroll on pages using ``drawer-open`` (files,
    chat). Combined with ``body { overflow: hidden }`` from ``base_with_navbar``,
    a wheel scroll on mobile then dragged the sticky navbar off-screen. The
    fix is a CSS rule locking ``html { overflow: hidden }`` whenever the body
    opts into the fixed-height layout.
    """

    def _render_base_with_navbar(self):
        tpl = Template("{% extends 'base_with_navbar.html' %}")
        return tpl.render(Context({}))

    def _body_classes(self, html):
        match = re.search(r'<body\b[^>]*\bclass="([^"]*)"', html)
        self.assertIsNotNone(match, "body tag with class attribute not found")
        return set(match.group(1).split())

    def test_html_overflow_is_locked_when_body_opts_into_fixed_layout(self):
        css = _stylesheet_without_whitespace()
        selector = "html:has(>body.overflow-hidden.h-dvh)"
        self.assertIn(selector, css)
        # The locking selector must declare overflow:hidden, not auto/visible.
        start = css.index(selector)
        self.assertIn("overflow:hidden", css[start : start + 120])

    def test_body_carries_classes_the_lock_selector_matches(self):
        # The lock fires on `body.overflow-hidden.h-dvh`. If the default body
        # class for base_with_navbar.html ever drops one of those classes,
        # the lock silently stops applying. Assert on membership, not the
        # full class string, so unrelated tailwind classes can come and go.
        classes = self._body_classes(self._render_base_with_navbar())
        self.assertIn("overflow-hidden", classes)
        self.assertIn("h-dvh", classes)


def _base_template_source() -> str:
    return Path(get_template("base.html").origin.name).read_text(encoding="utf-8")


class BaseTemplateScriptOriginTests(TestCase):
    """Alpine must never be served from a third-party CDN.

    Alpine evaluates every component expression, and its reactive state holds
    decrypted vault entries on the ``vault`` pages: a tampered third-party
    build would exfiltrate the whole vault.
    """

    def test_alpine_core_is_not_loaded_from_a_cdn(self):
        self.assertNotIn("cdn.jsdelivr.net/npm/alpinejs", _base_template_source())

    def test_alpine_plugins_are_not_loaded_from_a_cdn(self):
        source = _base_template_source()
        self.assertNotIn("cdn.jsdelivr.net/npm/@alpinejs", source)
        self.assertNotIn("cdn.jsdelivr.net/npm/@imacrayon", source)

    def test_alpine_is_loaded_from_the_vendored_bundle(self):
        self.assertIn("ui/js/vendor/alpine/alpine.js", _base_template_source())

    def test_lucide_is_not_loaded_from_a_cdn(self):
        # Lucide runs on every page and draws into the DOM; a tampered build
        # would have the same reach as a tampered Alpine. It also has to be
        # same-origin for the vault pages, which serve a policy that refuses
        # anything else.
        self.assertNotIn("unpkg.com", _base_template_source())

    def test_lucide_is_loaded_from_the_vendored_artifact(self):
        self.assertIn("ui/js/vendor/lucide/lucide.js", _base_template_source())

    def test_the_favicon_is_served_from_this_origin(self):
        # fav.farm renders an emoji server-side, which meant every page load
        # announced itself to a third party.
        self.assertNotIn("fav.farm", _base_template_source())

    def test_the_vendored_bundle_is_deferred(self):
        # Without `defer`, Alpine would start before stores.js (end of <body>,
        # not deferred) has attached its `alpine:init` listener: the stores
        # would never register and the navbar would raise on
        # $store.notifications.
        # Match the whole tag, not a line, so reformatting the attributes
        # across several lines cannot produce a false negative.
        tag = re.search(
            r"<script[^>]*ui/js/vendor/alpine/alpine\.js[^>]*>",
            _base_template_source(),
            re.DOTALL,
        )
        self.assertIsNotNone(tag, "alpine bundle script tag not found")
        # Match `defer` as a standalone attribute: a bare `assertIn` also
        # accepts `data-defer` or the substring inside a filename.
        self.assertRegex(tag.group(0), r"\sdefer(?=[\s=/>])")


class StandalonePageOriginTests(TestCase):
    """The two pages that do not extend ``base.html``.

    ``offline.html`` is the one the service worker serves when the network is
    gone, so an asset it fetches from a CDN is an asset it never gets: the
    page rendered as unstyled HTML every single time it was shown. ``500.html``
    is served while the application is already failing, and it announced the
    failure to two third parties.
    """

    def _offline_source(self) -> str:
        return (
            Path(__file__).resolve().parents[1] / "static" / "offline.html"
        ).read_text(encoding="utf-8")

    def _error_page_source(self) -> str:
        return Path(get_template("500.html").origin.name).read_text(encoding="utf-8")

    def test_the_offline_page_fetches_nothing_from_a_third_party(self):
        source = self._offline_source()
        for host in (
            "cdn.tailwindcss.com",
            "cdn.jsdelivr.net",
            "unpkg.com",
            "fav.farm",
        ):
            self.assertNotIn(host, source)

    def test_the_offline_page_styles_itself_from_this_origin(self):
        # Both the Tailwind CDN and the DaisyUI stylesheet it used to load are
        # already inside the built app.css.
        self.assertIn("/static/css/app.css", self._offline_source())

    def test_the_error_page_fetches_nothing_from_a_third_party(self):
        source = self._error_page_source()
        for host in (
            "cdn.tailwindcss.com",
            "cdn.jsdelivr.net",
            "unpkg.com",
            "fav.farm",
        ):
            self.assertNotIn(host, source)

    def test_the_error_page_renders(self):
        """It is rendered while the application is already broken: a template
        error here replaces it with Django's bare fallback text."""
        html = get_template("500.html").render({})
        self.assertIn("Something went wrong", html)
