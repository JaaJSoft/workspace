"""The pages the module serves under its own Content-Security-Policy must not
carry anything that policy refuses.

The browser test in ``tests/e2e/test_csp_browser.py`` is the proof; this is the
alarm that rings first. It runs in the module's own CI job, needs no browser,
and points at the offending tag - because the failure it guards against is
silent: an inline handler added to the shared navbar tomorrow does nothing on
every other page and breaks only here.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.vault.models import AccountIdentity

User = get_user_model()

# A <script> with no src, unless it is a data block - `json_script` output is
# never executed, so no policy applies to it.
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*type=[\"']application/json[\"'])[^>]*>",
    re.IGNORECASE,
)
INLINE_STYLE_BLOCK = re.compile(r"<style[\s>]", re.IGNORECASE)
# A rendered `style="..."`. Alpine's `:style` and `x-bind:style` are written by
# the CSSOM at runtime and are not what style-src-attr sees.
INLINE_STYLE_ATTR = re.compile(r"\sstyle=[\"']")
INLINE_EVENT_HANDLER = re.compile(r"\son[a-z]+=[\"']", re.IGNORECASE)
# Subresources the page fetches, not links the user clicks: an <a href> to
# another site is navigation, which no directive of this policy governs.
OFF_ORIGIN_ASSET = re.compile(
    r"\ssrc=[\"']https?://|<link[^>]+href=[\"']https?://", re.IGNORECASE
)
# Resource hints fetch nothing - a preconnect is a TLS handshake the policy
# does not govern - so they are cut out before the asset scan rather than
# whitelisted inside it.
RESOURCE_HINT = re.compile(
    r"<link[^>]*\brel=[\"'](?:preconnect|dns-prefetch)[\"'][^>]*>", re.IGNORECASE
)


class VaultPagesCarryNoInlineAssetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)

    def _pages(self):
        yield (
            "onboarding",
            self.client.get(reverse("vault_ui:onboarding")).content.decode(),
        )
        AccountIdentity.objects.create(
            user=self.user, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )
        yield "index", self.client.get(reverse("vault_ui:index")).content.decode()

    def _assert_absent(self, pattern, message, strip=None):
        for name, html in self._pages():
            if strip is not None:
                html = strip.sub("", html)
            with self.subTest(page=name):
                match = pattern.search(html)
                self.assertIsNone(
                    match,
                    f"{message}\n{html[max(0, match.start() - 200) : match.end() + 200]}"
                    if match
                    else message,
                )

    def test_no_executable_inline_script(self):
        self._assert_absent(
            INLINE_SCRIPT,
            "script-src 'self' refuses it: move the code to a file under static/",
        )

    def test_no_inline_style_block(self):
        self._assert_absent(
            INLINE_STYLE_BLOCK,
            "style-src 'self' refuses it: move the rules to scripts/frontend/input.css",
        )

    def test_no_inline_style_attribute(self):
        self._assert_absent(
            INLINE_STYLE_ATTR,
            "style-src-attr allows it today, but only because two vendored "
            "libraries need it - author markup with utility classes",
        )

    def test_no_inline_event_handler(self):
        self._assert_absent(
            INLINE_EVENT_HANDLER,
            "script-src 'self' refuses it: use data-dispatch, an Alpine "
            "listener, or the form attribute",
        )

    def test_every_asset_is_same_origin(self):
        self._assert_absent(
            OFF_ORIGIN_ASSET,
            "default-src 'none' refuses it: vendor the asset through scripts/frontend",
            strip=RESOURCE_HINT,
        )
