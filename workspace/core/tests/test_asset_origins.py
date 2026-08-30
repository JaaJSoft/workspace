"""No page may depend on a third-party CDN.

``scripts/frontend`` builds every vendored library into the static tree, so a
CDN reference is a page that breaks when jsDelivr is down, hands code
execution to a third party, and tells that party which pages the user opens.
``base.html`` used to carry a per-library guard; this one walks the whole tree
so the next reference is caught at CI time.
"""

import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]

THIRD_PARTY_HOSTS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdn.tailwindcss.com",
    "cdnjs.cloudflare.com",
    "esm.sh",
    # Renders the favicon emoji server-side: every page load announces itself.
    "fav.farm",
)

STATIC_TAG = re.compile(r"""{%\s*static\s+["']([^"']+)["']\s*%}""")
EMOJI_PICKER_TAG = re.compile(r"<emoji-picker\b[^>]*>", re.DOTALL)

# The two pages that do not extend base.html and so cannot inherit its assets.
STANDALONE_PAGES = ("files/ui/shared_file.html", "calendar/ui/polls/shared.html")


def _authored_sources():
    """Templates and hand-written static files.

    Vendored bundles are left out: a library may mention its own CDN (the emoji
    picker's default data source does), and the template is what overrides it.
    """
    for suffix in ("*.html", "*.js", "*.css"):
        for path in WORKSPACE.rglob(suffix):
            parts = set(path.relative_to(WORKSPACE).parts)
            if not parts & {"templates", "static"}:
                continue
            if parts & {"vendor", "tests", "node_modules"}:
                continue
            yield path


def _templates():
    return (path for path in _authored_sources() if path.suffix == ".html")


def _relative(path: Path) -> str:
    return path.relative_to(WORKSPACE).as_posix()


class ThirdPartyOriginTests(SimpleTestCase):
    def test_no_template_or_static_file_references_a_third_party_host(self):
        offenders = [
            f"{_relative(path)}: {host}"
            for path in _authored_sources()
            for host in THIRD_PARTY_HOSTS
            if host in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(
            offenders, [], "vendor the library through scripts/frontend instead"
        )

    def test_every_vendored_static_reference_resolves(self):
        # A misspelled artifact name, or a chunk the build renamed, is a 404
        # that only shows up on the one page that needs it.
        missing = [
            f"{_relative(path)}: {ref}"
            for path in _templates()
            for ref in STATIC_TAG.findall(path.read_text(encoding="utf-8"))
            if "/vendor/" in ref and finders.find(ref) is None
        ]
        self.assertEqual(missing, [])

    def test_every_emoji_picker_declares_a_vendored_data_source(self):
        # The element's built-in default for data-source is a jsDelivr URL: a
        # picker without the attribute fetches its emoji list from the CDN.
        tags = [
            tag
            for path in _templates()
            for tag in EMOJI_PICKER_TAG.findall(path.read_text(encoding="utf-8"))
        ]
        self.assertTrue(tags, "no <emoji-picker> element found in any template")
        for tag in tags:
            self.assertRegex(tag, r"""data-source="{%\s*static\s+["'][^"']*/vendor/""")

    def test_the_standalone_pages_load_the_vendored_stack(self):
        # They used to re-fetch a whole frontend stack (Tailwind play CDN,
        # DaisyUI, Alpine, Lucide) at versions drifting from the built ones.
        for name in STANDALONE_PAGES:
            source = Path(get_template(name).origin.name).read_text(encoding="utf-8")
            for asset in (
                "css/app.css",
                "ui/js/vendor/alpine/alpine.js",
                "ui/js/vendor/lucide/lucide.js",
            ):
                self.assertIn(asset, source, f"{name} does not load {asset}")
