"""Render the Workspace presentation video.

Captures every module from a seeded throwaway demo, lays the captures out on
an animated stage (``scripts/presentation/stage/``), synthesizes a soundtrack
locked to the same tempo, and encodes the whole thing with ffmpeg:

    uv run python scripts/presentation_video.py               # full build
    uv run python scripts/presentation_video.py --skip-capture
    uv run python scripts/presentation_video.py --player-only # no mp4, just the player

Everything lands in ``build/presentation/`` (git-ignored). ``index.html`` in
there is a self-contained player: open it in a browser to watch the video live
with its music, scrub with the arrow keys - the fastest way to iterate on the
animation, since the mp4 is rendered from the very same page frame by frame.

What the video says lives in ``scripts/presentation/scenes.py``; a new module
on the home dashboard shows up on its own with a generic scene until it gets
a proper entry there.

Requirements: the ``dev`` dependency group (Playwright), a Chromium install
(``uv run playwright install chromium``) and ``ffmpeg`` on the PATH.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_DIR = Path(__file__).resolve().parent / "presentation" / "stage"
LUCIDE_JS = REPO_ROOT / "workspace/common/static/ui/js/vendor/lucide/lucide.js"
DEFAULT_OUT = REPO_ROOT / "build" / "presentation"

WIDTH, HEIGHT = 1920, 1080
BPM = 120
BEATS_PER_BAR = 4
INTRO_BARS = 4
SCENE_BARS = 4
PLATFORM_BARS = 4
OUTRO_BARS = 4
# The last chord rings out under the closing card.
TAIL_BARS = 2


def resolve_scenes():
    """SCENES merged with the module registry, plus generic fallbacks."""
    from scripts.presentation.scenes import SCENES
    from workspace.core.module_registry import registry

    modules = {m.slug: m for m in registry.get_all()}
    resolved = []
    for index, scene in enumerate(SCENES):
        module = modules.get(scene["slug"])
        if module is None and "name" not in scene:
            print(f"  ! scene '{scene['slug']}' names no registered module, skipped")
            continue
        resolved.append(_scene(scene, module, index))

    covered = {s["slug"] for s in SCENES}
    for module in sorted(modules.values(), key=lambda m: m.order):
        if module.slug in covered or not module.show_on_dashboard:
            continue
        print(
            f"  ! module '{module.slug}' has no scene in scenes.py - "
            "using a generic one, write it a proper entry"
        )
        generic = {
            "slug": module.slug,
            "tagline": module.description,
            "features": [],
            "shots": [{"path": module.url or "/"}],
        }
        resolved.append(_scene(generic, module, len(resolved)))
    return resolved


def _scene(scene, module, index):
    from scripts.presentation.scenes import HUES

    color = scene.get("color") or (module.color if module else "brand")
    return {
        "key": scene["slug"],
        "slug": scene["slug"],
        "name": scene.get("name") or module.name,
        "icon": scene.get("icon") or module.icon,
        "color": HUES.get(color, HUES["brand"]),
        "preview": bool(module and module.preview),
        "tagline": scene["tagline"],
        "features": scene["features"],
        "shots": scene["shots"],
    }


def build_timeline(scenes, fps):
    from scripts.presentation.capture import shot_filename
    from scripts.presentation.scenes import (
        BRAND_FROM,
        BRAND_TO,
        CAPTURE_VIEWPORT,
        HUES,
        INTRO,
        OUTRO,
        PLATFORM,
    )

    bar = 60 / BPM * BEATS_PER_BAR
    sections = []
    cursor = 0

    def add(kind, bars, **data):
        nonlocal cursor
        sections.append({"kind": kind, "start": cursor * bar, "bars": bars, **data})
        cursor += bars

    # drumBars: how many bars from the section start the drums play. The
    # soundtrack arranges around it and the stage pulses with it.
    add("intro", INTRO_BARS, color=HUES["brand"], drumBars=0, **INTRO)
    for number, scene in enumerate(scenes, start=1):
        shots = [
            {
                "src": f"shots/{shot_filename(scene['key'], i)}",
                # What the address bar shows: no query, no uuid placeholders.
                "path": re.sub(r"/\{[^}]+\}", "", shot["path"].split("?")[0]) or "/",
                "callout": shot.get("callout"),
            }
            for i, shot in enumerate(scene["shots"])
        ]
        add(
            "module",
            SCENE_BARS,
            drumBars=SCENE_BARS,
            number=number,
            **{k: scene[k] for k in ("key", "name", "icon", "color", "preview")},
            tagline=scene["tagline"],
            features=[list(f) for f in scene["features"]],
            shots=shots,
        )
    add(
        "platform",
        PLATFORM_BARS,
        drumBars=0,
        color=HUES["brand"],
        title=PLATFORM["title"],
        tagline=PLATFORM["tagline"],
        cards=[list(c) for c in PLATFORM["cards"]],
        shots={
            name: f"shots/{shot_filename('platform', name)}"
            for name in PLATFORM["shots"]
        },
    )
    add("outro", OUTRO_BARS + TAIL_BARS, drumBars=2, color=HUES["brand"], **OUTRO)

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "fps": fps,
        "bpm": BPM,
        "beatsPerBar": BEATS_PER_BAR,
        "duration": cursor * bar,
        "capture": CAPTURE_VIEWPORT,
        "brand": [BRAND_FROM, BRAND_TO],
        "modules": [
            {"icon": s["icon"], "color": s["color"], "name": s["name"]} for s in scenes
        ],
        "sections": sections,
    }


def write_player(out_dir: Path, timeline):
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in STAGE_DIR.iterdir():
        shutil.copy2(src, out_dir / src.name)
    shutil.copy2(LUCIDE_JS, out_dir / "lucide.js")
    # A script, not JSON: the player must also work straight from file://,
    # where fetch() is refused.
    (out_dir / "timeline.js").write_text(
        "window.TIMELINE = " + json.dumps(timeline, indent=1) + ";\n",
        encoding="utf-8",
    )


def open_stage(p, out_dir: Path):
    from scripts.screenshots import chromium_path

    browser = p.chromium.launch(executable_path=chromium_path())
    page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto((out_dir / "index.html").as_uri() + "?render")
    page.wait_for_function("window.stage && window.stage.ready", timeout=60000)
    if errors:
        raise RuntimeError(f"stage failed to load: {errors}")
    missing = page.evaluate("window.stage.missingIcons")
    if missing:
        print(f"  ! unknown lucide icons, fix scenes.py: {missing}")
    return browser, page


def render_stills(out_dir: Path, times):
    from playwright.sync_api import sync_playwright

    stills = out_dir / "stills"
    stills.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser, page = open_stage(p, out_dir)
        for t in times:
            page.evaluate("t => window.stage.render(t)", t)
            target = stills / f"t{t:06.2f}.png"
            page.screenshot(path=target)
            print(f"  {target}")
        browser.close()


def render_video(out_dir: Path, timeline, audio: Path, output: Path):
    from playwright.sync_api import sync_playwright

    fps = timeline["fps"]
    frames = round(timeline["duration"] * fps)
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-i", str(audio),
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-tune", "animation",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(output),
        ],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    started = time.monotonic()
    with sync_playwright() as p:
        browser, page = open_stage(p, out_dir)
        for frame in range(frames):
            page.evaluate("t => window.stage.render(t)", frame / fps)
            ffmpeg.stdin.write(page.screenshot(type="jpeg", quality=94))
            if frame % fps == 0:
                elapsed = time.monotonic() - started
                eta = elapsed / (frame + 1) * (frames - frame - 1)
                print(
                    f"\r  frame {frame}/{frames}  (eta {eta:4.0f}s)", end="", flush=True
                )
        browser.close()
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--skip-capture",
        action="store_true",
        help="reuse the screenshots of a previous run",
    )
    parser.add_argument(
        "--player-only",
        action="store_true",
        help="build the browser player, do not encode the mp4",
    )
    parser.add_argument(
        "--stills",
        nargs="+",
        type=float,
        metavar="SECONDS",
        help="write still frames at these instants to stills/ instead of the mp4",
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--music",
        type=Path,
        help="use this audio file instead of the synthesized soundtrack",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    os.chdir(REPO_ROOT)
    out_dir = args.out_dir.resolve()
    shots_dir = out_dir / "shots"

    if args.skip_capture:
        import django

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
        django.setup()
        scenes = resolve_scenes()
    else:
        from scripts.presentation.capture import capture_all
        from scripts.presentation.scenes import PLATFORM

        print("Capturing the demo...")
        scenes = capture_all(resolve_scenes, PLATFORM, shots_dir)

    timeline = build_timeline(scenes, args.fps)
    missing = [
        shot["src"]
        for section in timeline["sections"]
        if section["kind"] == "module"
        for shot in section["shots"]
        if not (out_dir / shot["src"]).is_file()
    ]
    if missing:
        sys.exit(f"Missing captures (run without --skip-capture): {missing}")

    print(f"Writing the player to {out_dir}/ ...")
    write_player(out_dir, timeline)

    if args.music:
        audio = out_dir / ("soundtrack" + args.music.suffix)
        shutil.copy2(args.music, audio)
    else:
        from scripts.presentation.soundtrack import render_soundtrack

        print("Synthesizing the soundtrack...")
        audio = out_dir / "soundtrack.wav"
        render_soundtrack(timeline, audio)
    (out_dir / "audio.js").write_text(
        f"window.SOUNDTRACK = {json.dumps(audio.name)};\n", encoding="utf-8"
    )

    if args.stills:
        render_stills(out_dir, args.stills)
        return
    if args.player_only:
        print(f"Done. Open {out_dir / 'index.html'}")
        return
    output = out_dir / "workspace.mp4"
    print(f"Rendering {output} ...")
    render_video(out_dir, timeline, audio, output)
    print(f"Done. {output}")


if __name__ == "__main__":
    main()
