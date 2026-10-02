"""Keeps the promo video (scripts/promo_video.py) in step with the app.

The video is rendered by hand, so nothing else notices when it falls behind:
a module added to the dashboard would never be filmed, and an icon renamed in
a Lucide upgrade would render as an empty tile.
"""

import re
import sys

from django.conf import settings
from django.test import SimpleTestCase

from workspace.core.module_registry import registry

LUCIDE = (
    settings.BASE_DIR / "workspace/common/static/ui/js/vendor/lucide/lucide.js"
).read_text(encoding="utf-8")

if str(settings.BASE_DIR) not in sys.path:
    sys.path.insert(0, str(settings.BASE_DIR))

from scripts.promo.takes import TAKES  # noqa: E402
from scripts.promo_video import SCENES, SOURCE, render_feature, timeline  # noqa: E402


def _pascal(name):
    return "".join(part[:1].upper() + part[1:] for part in name.split("-"))


def _compositions():
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in (SOURCE / "compositions").glob("*.html")
    }


class PromoVideoTests(SimpleTestCase):
    def test_every_dashboard_module_has_a_take(self):
        missing = sorted(
            m.slug
            for m in registry.get_all()
            if m.show_on_dashboard and m.slug not in TAKES
        )
        self.assertEqual(missing, [], "film these modules in scripts/promo/takes.py")

    def test_every_scene_has_a_composition_filling_its_timeline(self):
        plan = {s["id"]: s for s in timeline()["scenes"]}
        compositions = _compositions()
        for scene in SCENES:
            with self.subTest(scene["id"]):
                if "feature" in scene:
                    html = render_feature(plan[scene["id"]])
                else:
                    html = compositions[scene["id"]]
                self.assertIn(f'data-composition-id="{scene["id"]}"', html)
                self.assertIn(f'window.promo.scene("{scene["id"]}")', html)
                self.assertIn(f'window.__timelines["{scene["id"]}"] = tl;', html)

    def test_the_promo_stays_an_ad(self):
        self.assertLessEqual(timeline()["duration"], 90)

    def test_every_take_a_scene_plays_is_filmed(self):
        played = {s["feature"]["take"] for s in SCENES if "feature" in s}
        for html in _compositions().values():
            played |= set(re.findall(r"assets/takes/([a-z]+)\.mp4", html))
        self.assertLessEqual(played, set(TAKES))

    def test_every_icon_exists_in_the_vendored_lucide(self):
        icons = {s["feature"]["icon"] for s in SCENES if "feature" in s}
        for html in _compositions().values():
            icons |= set(re.findall(r'data-lucide="([a-z0-9-]+)"', html))
            # Scenes that build their panels in script name icons in a table.
            icons |= set(re.findall(r'\["#[0-9a-f]{6}", "([a-z0-9-]+)"\]', html))
        unknown = sorted(
            name
            for name in icons
            if not re.search(rf"\.{re.escape(_pascal(name))}=", LUCIDE)
        )
        self.assertEqual(unknown, [])
