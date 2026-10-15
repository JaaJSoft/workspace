"""Browser history has one owner: ``common/static/ui/js/nav_history.js``.

It tags every entry with the page load and the content it shows, which is
what lets a back or forward tell a stale page from a current one and a back
button tell an in-app page from the outside world. An entry written straight
through ``history.pushState`` or a ``popstate`` listener of its own bypasses
that, and each module that did so grew its own bug: content left on screen
under another URL, a back that reopened what was just closed, a back that left
the app. Modules go through ``navHistory`` and listen to ``nav:restore``.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

WORKSPACE = Path(__file__).resolve().parents[2]
OWNER = WORKSPACE / "common" / "static" / "ui" / "js" / "nav_history.js"
# Rendered when the app itself failed: it loads no script, nav_history.js
# included, so its Go back link is the browser's plain one.
STANDALONE = (WORKSPACE / "core" / "templates" / "500.html",)

FORBIDDEN = re.compile(
    r"history\.(pushState|replaceState|back|forward|go)\b"
    r"|\bonpopstate\b|['\"]popstate['\"]|@popstate"
)


def _authored_sources():
    for suffix in ("*.html", "*.js"):
        for path in WORKSPACE.rglob(suffix):
            parts = set(path.relative_to(WORKSPACE).parts)
            if not parts & {"templates", "static"}:
                continue
            if parts & {"vendor", "tests", "node_modules"}:
                continue
            if path == OWNER or path in STANDALONE:
                continue
            yield path


class NavHistoryOwnerTests(SimpleTestCase):
    def test_no_module_writes_browser_history_itself(self):
        offenders = [
            f"{path.relative_to(WORKSPACE)}:{number}: {line.strip()}"
            for path in _authored_sources()
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if FORBIDDEN.search(line)
        ]

        self.assertEqual(
            offenders,
            [],
            "Use navHistory (push, replace, returnTo, back) and nav:restore",
        )

    def test_the_owner_still_matches_what_this_test_forbids(self):
        source = OWNER.read_text(encoding="utf-8")

        self.assertIn("history.pushState", source)
        self.assertIn("'popstate'", source)
