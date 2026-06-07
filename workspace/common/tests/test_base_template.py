"""Regression tests for ``base.html`` / ``base_with_navbar.html``."""

import re

from django.template import Context, Template
from django.test import TestCase


class NavbarLayoutScrollLockTests(TestCase):
    """DaisyUI emits ``html:has(.drawer-open.drawer-open) { overflow-y: auto }``
    which lets the html element scroll on pages using ``drawer-open`` (files,
    chat). Combined with ``body { overflow: hidden }`` from ``base_with_navbar``,
    a wheel scroll on mobile then dragged the sticky navbar off-screen. The
    fix is a CSS rule in ``base.html`` that locks ``html { overflow: hidden }``
    whenever the body opts into the fixed-height layout.
    """

    def _render_base_with_navbar(self):
        tpl = Template("{% extends 'base_with_navbar.html' %}")
        return tpl.render(Context({}))

    def _body_classes(self, html):
        match = re.search(r'<body\b[^>]*\bclass="([^"]*)"', html)
        self.assertIsNotNone(match, "body tag with class attribute not found")
        return set(match.group(1).split())

    def test_html_overflow_is_locked_when_body_opts_into_fixed_layout(self):
        html = self._render_base_with_navbar()
        self.assertIn("html:has(> body.overflow-hidden.h-dvh)", html)
        # The locking selector must declare overflow:hidden, not auto/visible.
        # Pull a slice around the selector and verify the declaration is present.
        idx = html.index("html:has(> body.overflow-hidden.h-dvh)")
        block = html[idx : idx + 200]
        self.assertIn("overflow: hidden", block)

    def test_body_carries_classes_the_lock_selector_matches(self):
        # The lock fires on `body.overflow-hidden.h-dvh`. If the default body
        # class for base_with_navbar.html ever drops one of those classes,
        # the lock silently stops applying. Assert on membership, not the
        # full class string, so unrelated tailwind classes can come and go.
        classes = self._body_classes(self._render_base_with_navbar())
        self.assertIn("overflow-hidden", classes)
        self.assertIn("h-dvh", classes)
