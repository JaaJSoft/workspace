"""Keeps the promo video (scripts/promo_video.py) in step with the app.

The video is rendered by hand, so nothing else notices when it falls behind:
a module added to the dashboard would never be filmed, and an icon renamed in
a Lucide upgrade would render as an empty tile.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase

from workspace.core.module_registry import registry

LUCIDE = (
    settings.BASE_DIR / "workspace/common/static/ui/js/vendor/lucide/lucide.js"
).read_text(encoding="utf-8")

if str(settings.BASE_DIR) not in sys.path:
    sys.path.insert(0, str(settings.BASE_DIR))

from scripts import promo_video  # noqa: E402
from scripts.promo import takes  # noqa: E402
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


class PromoBuildTests(SimpleTestCase):
    def _run_main(self, argv, present, snapshot_rc=0):
        """Run main() with these takes on disk; returns the takes it filmed."""
        filmed = []
        done = subprocess.CompletedProcess([], snapshot_rc)
        with tempfile.TemporaryDirectory() as tmp:
            takes_dir = Path(tmp) / "takes"
            takes_dir.mkdir()
            for name in present:
                (takes_dir / f"{name}.mp4").write_bytes(b"")
            argv = ["promo_video.py", *argv, "--out-dir", tmp]
            with (
                patch.object(sys, "argv", argv),
                patch.object(promo_video, "TAKES_DIR", takes_dir),
                patch.object(promo_video, "film", side_effect=filmed.extend),
                patch.object(promo_video, "write_project"),
                patch.object(promo_video, "hyperframes", return_value=done),
                patch("scripts.promo.soundtrack.render_soundtrack"),
            ):
                promo_video.main()
        return filmed

    def test_a_partial_refilm_also_films_the_missing_takes(self):
        present = [name for name in TAKES if name not in ("chat", "vault")]
        filmed = self._run_main(["--refilm", "files", "--stills", "1"], present)
        self.assertEqual(sorted(filmed), ["chat", "files", "vault"])

    def test_a_failed_snapshot_fails_the_run(self):
        with self.assertRaises(SystemExit) as raised:
            self._run_main(["--stills", "1"], list(TAKES), snapshot_rc=1)
        self.assertTrue(raised.exception.code)

    def test_write_project_installs_the_toolchain_it_copies_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            modules = tmp / "node_modules"
            vendor = {name: modules / name for name in promo_video.VENDOR}

            def npm_ci(cmd, **kwargs):
                (modules / "hyperframes").mkdir(parents=True)
                for source in vendor.values():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(b"")
                return subprocess.CompletedProcess(cmd, 0)

            (tmp / "takes").mkdir()
            audio = tmp / "music.wav"
            audio.write_bytes(b"")
            with (
                patch.object(promo_video, "NODE_MODULES", modules),
                patch.object(promo_video, "VENDOR", vendor),
                patch.object(promo_video, "TAKES_DIR", tmp / "takes"),
                patch("scripts.promo_video.shutil.which", return_value="npm"),
                patch("scripts.promo_video.subprocess.run", side_effect=npm_ci) as run,
            ):
                promo_video.write_project(
                    tmp / "project", promo_video.FORMATS["landscape"], timeline(), audio
                )
            run.assert_called_once()
            self.assertTrue((tmp / "project/vendor/gsap.min.js").is_file())

    def test_the_assistant_take_fails_without_an_answer(self):
        page = MagicMock()
        page.get_by_text.return_value.count.return_value = 0
        with (
            patch.object(takes, "_send"),
            patch.object(takes, "wait"),
            patch.object(takes.time, "monotonic", side_effect=[0, 11, 11, 11]),
            self.assertRaises(RuntimeError),
        ):
            takes._ask_assistant(page)
