"""Regression tests for ``base.html`` / ``base_with_navbar.html``."""
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

    def test_html_overflow_is_locked_when_body_opts_into_fixed_layout(self):
        html = self._render_base_with_navbar()
        self.assertIn("html:has(> body.overflow-hidden.h-dvh)", html)
        # The locking selector must declare overflow:hidden, not auto/visible.
        # Pull a slice around the selector and verify the declaration is present.
        idx = html.index("html:has(> body.overflow-hidden.h-dvh)")
        block = html[idx:idx + 200]
        self.assertIn("overflow: hidden", block)

    def test_body_opts_into_fixed_layout_via_default_body_class(self):
        html = self._render_base_with_navbar()
        # The lock relies on the body carrying BOTH classes the selector
        # matches. If the default body class for base_with_navbar.html ever
        # drops one of them, the lock silently stops applying.
        self.assertIn('class="bg-base-100 h-dvh overflow-hidden flex flex-col"', html)
