"""Render the Workspace presentation video.

Films real interactions in every module on a seeded throwaway demo (a visible
cursor searching, dragging, typing), synthesizes a soundtrack locked to the
video's tempo, and writes a HyperFrames composition that plays the takes in
animated windows (``scripts/presentation/composition/``), then renders it:

    uv run python scripts/presentation_video.py                 # full build
    uv run python scripts/presentation_video.py --skip-capture  # reuse the takes
    uv run python scripts/presentation_video.py --skip-capture --preview
    uv run python scripts/presentation_video.py --serve         # keep a demo up...
    uv run python scripts/presentation_video.py --only chat     # ...and refilm on it

Everything lands in ``build/presentation/`` (git-ignored), which is a plain
HyperFrames project: ``--preview`` opens it in HyperFrames Studio to scrub the
timeline with its music. See ``scripts/presentation/README.md``.

Requirements: the ``dev`` dependency group (Playwright) with a Chromium
install (``uv run playwright install chromium``) for the takes, Node.js 22+
and ``ffmpeg`` on the PATH. The HyperFrames toolchain is pinned in
``scripts/presentation/package.json`` and installed on first run.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLCHAIN = REPO_ROOT / "scripts" / "presentation"
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
        section = {"kind": kind, "key": kind, "start": cursor * bar, "bars": bars}
        sections.append({**section, **data})
        cursor += bars

    # drumBars: how many bars from the section start the drums play. The
    # soundtrack arranges around it and the stage pulses with it.
    add("intro", INTRO_BARS, color=HUES["brand"], drumBars=0, **INTRO)
    for number, scene in enumerate(scenes, start=1):
        shots = [
            {
                "src": shot_filename(scene["key"], i, shot),
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
        shots={name: shot_filename("platform", name) for name in PLATFORM["shots"]},
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


def hyperframes(*args):
    """Run the pinned HyperFrames CLI, installing it on first use."""
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if not npm or not npx:
        sys.exit("Node.js 22+ is required (npm and npx on the PATH).")
    if not (TOOLCHAIN / "node_modules" / "hyperframes").is_dir():
        print("Installing the HyperFrames toolchain...")
        subprocess.run(
            [npm, "ci", "--no-fund", "--no-audit"], cwd=TOOLCHAIN, check=True
        )
    env = {
        **os.environ,
        # A repository script has no business reporting usage anywhere.
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "HYPERFRAMES_SKIP_SKILLS": "1",
    }
    return subprocess.run(
        [npx, "--no-install", "hyperframes", *args], cwd=TOOLCHAIN, env=env
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--skip-capture",
        action="store_true",
        help="reuse the captures of a previous run",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="SCENE",
        help="capture only these scenes (module slugs, or platform), keep the rest",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="keep a seeded demo running for later captures, until Ctrl+C",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="open the composition in HyperFrames Studio instead of rendering",
    )
    parser.add_argument(
        "--stills",
        nargs="+",
        type=float,
        metavar="SECONDS",
        help="snapshot these instants instead of rendering the mp4",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="fast, lower-quality render for iterating",
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

    from scripts.presentation import capture
    from scripts.presentation.scenes import PLATFORM

    if args.serve:
        capture.serve(out_dir)
        return

    running = None if args.skip_capture else capture.running_demo(out_dir)
    if args.skip_capture or running:
        import django

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
        django.setup()
        scenes = resolve_scenes()
        if running:
            # A demo left up by --serve: fast, but the takes act on the data
            # the previous ones changed. Render the final video without it.
            print(f"Capturing the running demo at {running[0]}...")
            capture.capture_all(scenes, PLATFORM, out_dir, *running, only=args.only)
    else:
        print("Capturing a fresh demo...")
        with capture.demo() as (base_url, context):
            scenes = resolve_scenes()
            capture.capture_all(
                scenes, PLATFORM, out_dir, base_url, context, only=args.only
            )

    timeline = build_timeline(scenes, args.fps)
    missing = [
        src
        for section in timeline["sections"]
        for src in (
            [shot["src"] for shot in section["shots"]]
            if section["kind"] == "module"
            else list(section.get("shots", {}).values())
        )
        if not (out_dir / src).is_file()
    ]
    if missing:
        sys.exit(f"Missing captures (run without --skip-capture): {missing}")

    out_dir.mkdir(parents=True, exist_ok=True)
    if args.music:
        audio = out_dir / ("soundtrack" + args.music.suffix)
        shutil.copy2(args.music, audio)
    else:
        from scripts.presentation.soundtrack import render_soundtrack

        print("Synthesizing the soundtrack...")
        audio = out_dir / "soundtrack.wav"
        render_soundtrack(timeline, audio)

    from scripts.presentation.composition import write_project

    print(f"Writing the composition to {out_dir}/ ...")
    write_project(out_dir, timeline, audio.name)

    if args.preview:
        hyperframes("preview", str(out_dir))
        return
    if args.stills:
        at = ",".join(f"{t:g}" for t in args.stills)
        sys.exit(hyperframes("snapshot", str(out_dir), "--at", at).returncode)
    if hyperframes("check", str(out_dir)).returncode:
        sys.exit("The composition does not pass `hyperframes check`.")
    output = out_dir / "workspace.mp4"
    print(f"Rendering {output} ...")
    result = hyperframes(
        "render",
        str(out_dir),
        "--output",
        str(output),
        "--fps",
        str(args.fps),
        "--quality",
        "draft" if args.draft else "delivery",
    )
    if result.returncode:
        sys.exit("The render failed.")
    print(f"Done. {output}")


if __name__ == "__main__":
    main()
