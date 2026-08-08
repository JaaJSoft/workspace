"""Regression tests for ``base.html`` / ``base_with_navbar.html``."""

import re
from pathlib import Path

from django.template import Context, Template
from django.template.loader import get_template
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


def _base_template_source() -> str:
    return Path(get_template("base.html").origin.name).read_text(encoding="utf-8")


class BaseTemplateScriptOriginTests(TestCase):
    """Alpine ne doit jamais être servi depuis un CDN tiers.

    Alpine exécute les expressions de tous les composants et son état réactif
    contient les entrées de coffre déchiffrées sur les pages du module
    ``passwords``. Un build altéré servi par un tiers exfiltrerait donc le coffre
    entier. jsDelivr servait de surcroît une plage flottante ``3.x.x``, ce qui
    rendait tout épinglage impossible.

    Ce test lit le gabarit sur disque via le chargeur Django plutôt qu'un chemin
    codé en dur, pour rester valide si l'arborescence des gabarits bouge.
    """

    def test_alpine_core_is_not_loaded_from_a_cdn(self):
        self.assertNotIn("cdn.jsdelivr.net/npm/alpinejs", _base_template_source())

    def test_alpine_plugins_are_not_loaded_from_a_cdn(self):
        source = _base_template_source()
        self.assertNotIn("cdn.jsdelivr.net/npm/@alpinejs", source)
        self.assertNotIn("cdn.jsdelivr.net/npm/@imacrayon", source)

    def test_alpine_is_loaded_from_the_vendored_bundle(self):
        self.assertIn("ui/js/vendor/alpine/alpine.js", _base_template_source())

    def test_the_vendored_bundle_is_deferred(self):
        # Sans `defer`, le bundle s'exécuterait pendant l'analyse du <head>,
        # donc AVANT que stores.js (fin de <body>, non différé) ait posé son
        # écouteur `alpine:init` — les trois stores ne seraient jamais
        # enregistrés et la navbar lèverait sur $store.notifications.
        source = _base_template_source()
        line = next(
            line for line in source.splitlines()
            if "ui/js/vendor/alpine/alpine.js" in line
        )
        self.assertIn("defer", line)
