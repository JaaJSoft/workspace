"""Keeps the presentation video (scripts/presentation_video.py) in step with the app.

The video is rendered by hand before a release, so nothing else notices when
it falls behind: a module added to the dashboard would only get a generic
scene, and an icon renamed in a Lucide upgrade would render as an empty tile.
"""

import re
import sys
from itertools import pairwise

from django.conf import settings
from django.test import SimpleTestCase

from workspace.core.module_registry import registry

LUCIDE = (
    settings.BASE_DIR / "workspace/common/static/ui/js/vendor/lucide/lucide.js"
).read_text(encoding="utf-8")

if str(settings.BASE_DIR) not in sys.path:
    sys.path.insert(0, str(settings.BASE_DIR))

from scripts.presentation.composition import render_index, render_scene  # noqa: E402
from scripts.presentation.scenes import OUTRO, PLATFORM, SCENES  # noqa: E402
from scripts.presentation_video import build_timeline, resolve_scenes  # noqa: E402


def _pascal(name):
    return "".join(part[:1].upper() + part[1:] for part in name.split("-"))


class PresentationVideoTests(SimpleTestCase):
    def test_every_dashboard_module_has_a_scene(self):
        covered = {scene["slug"] for scene in SCENES}
        missing = sorted(
            m.slug
            for m in registry.get_all()
            if m.show_on_dashboard and m.slug not in covered
        )
        self.assertEqual(
            missing,
            [],
            "give these modules a scene in scripts/presentation/scenes.py",
        )

    def test_every_icon_exists_in_the_vendored_lucide(self):
        icons = {OUTRO["url_icon"]}
        icons |= {scene["icon"] for scene in resolve_scenes()}
        icons |= {icon for scene in SCENES for icon, _ in scene["features"]}
        icons |= {icon for icon, _, _ in PLATFORM["cards"]}
        unknown = sorted(
            name
            for name in icons
            if not re.search(rf"\.{re.escape(_pascal(name))}=", LUCIDE)
        )
        self.assertEqual(unknown, [])

    def test_timeline_covers_every_scene_back_to_back(self):
        timeline = build_timeline(resolve_scenes(), fps=30)
        sections = timeline["sections"]
        self.assertEqual(
            [s["key"] for s in sections if s["kind"] == "module"],
            [s["slug"] for s in SCENES],
        )
        bar = 60 / timeline["bpm"] * timeline["beatsPerBar"]
        for before, after in pairwise(sections):
            self.assertAlmostEqual(
                before["start"] + before["bars"] * bar, after["start"]
            )
        last = sections[-1]
        self.assertAlmostEqual(last["start"] + last["bars"] * bar, timeline["duration"])

    def test_every_scene_is_mounted_and_fills_its_timeline(self):
        # A sub-composition whose script registers its timeline without
        # building it still passes `hyperframes check`: the scene renders as
        # its static end state, every animation silently gone.
        timeline = build_timeline(resolve_scenes(), fps=30)
        index = render_index(timeline, "soundtrack.wav")
        self.assertIn('<audio id="soundtrack" src="soundtrack.wav"', index)
        for i, section in enumerate(timeline["sections"]):
            with self.subTest(section["key"]):
                self.assertIn(
                    f'data-composition-src="compositions/{section["key"]}.html"', index
                )
                scene = render_scene(timeline, i)
                self.assertIn(f"window.presentation.scene(tl, el, {i});", scene)
                self.assertIn(
                    f'window.__timelines["scene-{section["key"]}"] = tl;', scene
                )


class PromoVideoTests(SimpleTestCase):
    def test_every_scene_has_a_composition_filling_its_timeline(self):
        from scripts.promo_video import SCENES, SOURCE

        for scene in SCENES:
            with self.subTest(scene["id"]):
                path = SOURCE / "compositions" / f"{scene['id']}.html"
                html = path.read_text(encoding="utf-8")
                self.assertIn(f'data-composition-id="{scene["id"]}"', html)
                self.assertIn(f'window.promo.scene("{scene["id"]}")', html)
                self.assertIn(f'window.__timelines["{scene["id"]}"] = tl;', html)

    def test_the_promo_stays_an_ad(self):
        from scripts.promo_video import timeline

        self.assertLessEqual(timeline()["duration"], 90)

    def test_every_take_a_scene_plays_is_filmed_by_the_presentation(self):
        from scripts.promo_video import SOURCE, TAKE_NAMES

        played = set()
        for path in (SOURCE / "compositions").glob("*.html"):
            played |= set(
                re.findall(
                    r"assets/clips/([a-z]+)-0\.mp4", path.read_text(encoding="utf-8")
                )
            )
        self.assertLessEqual(played, set(TAKE_NAMES))
        self.assertLessEqual(set(TAKE_NAMES), {s["slug"] for s in SCENES})
