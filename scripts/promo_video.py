"""Render the Workspace promo: a one-minute, beat-cut ad in 16:9 and 9:16.

The long tour (``scripts/presentation_video.py``) walks through every module;
this is its trailer. It reuses that pipeline's filmed takes, its soundtrack
synthesizer and its pinned HyperFrames toolchain, and cuts them to 128 BPM
around one message: the tools a team juggles fit in one app it hosts itself.

    uv run python scripts/promo_video.py                  # both formats
    uv run python scripts/promo_video.py --aspect portrait
    uv run python scripts/promo_video.py --preview        # HyperFrames Studio (16:9)
    uv run python scripts/promo_video.py --stills 3 12 40 # snapshots only

The scenes are hand-written HyperFrames sub-compositions in
``scripts/promo/compositions/``; this script owns the timing (scene lengths in
bars, the transitions between them, the music) and assembles one project per
format under ``build/promo/``. The takes come from ``build/presentation/clips``;
when they are missing, the presentation pipeline films them first.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "scripts" / "promo"
DEFAULT_OUT = REPO_ROOT / "build" / "promo"
TAKES = REPO_ROOT / "build" / "presentation" / "clips"

BPM = 128
BEATS_PER_BAR = 4
# Scenes overlap by this much, so a transition has both sides on screen.
OVERLAP = 0.3

FORMATS = {
    "landscape": {"width": 1920, "height": 1080, "suffix": "16x9"},
    "portrait": {"width": 1080, "height": 1920, "suffix": "9x16"},
}

# In playing order: the sub-composition, its length in bars, how it is cut
# in, and the part of the music it sits on (see soundtrack.py: "intro" has
# no drums and ends on a riser, "module" plays the full groove, "platform"
# is the breakdown, "outro" drops back in and rings out).
SCENES = [
    {"id": "hook", "bars": 4, "cut": None, "music": "intro"},
    {"id": "intro", "bars": 2, "cut": "zoom", "music": "intro"},
    {"id": "search", "bars": 2, "cut": "flash", "music": "module"},
    {"id": "projects", "bars": 2, "cut": "whip-left", "music": "module"},
    {"id": "talk", "bars": 2, "cut": "whip-right", "music": "module"},
    {"id": "files", "bars": 2, "cut": "zoom", "music": "module"},
    {"id": "cycle", "bars": 4, "cut": "blocks", "music": "module"},
    {"id": "wall", "bars": 2, "cut": "zoom-back", "music": "module"},
    {"id": "yours", "bars": 4, "cut": "blur", "music": "platform"},
    {"id": "cta", "bars": 6, "cut": "zoom", "music": "outro"},
]

# The takes each scene plays, from the presentation pipeline.
TAKE_NAMES = [
    "dashboard",
    "files",
    "photos",
    "notes",
    "chat",
    "ai",
    "mail",
    "calendar",
    "projects",
    "people",
    "vault",
]


def timeline():
    """Scene windows and the music sections, from the bar counts above."""
    beat = 60 / BPM
    bar = beat * BEATS_PER_BAR
    scenes, cursor = [], 0
    for scene in SCENES:
        start = cursor * bar
        lead = OVERLAP if cursor else 0.0
        scenes.append(
            {
                **scene,
                "start": start,
                "duration": scene["bars"] * bar,
                # The slot opens early by `lead` so the cut can overlap.
                "slot_start": start - lead,
                "lead": lead,
            }
        )
        cursor += scene["bars"]

    # Consecutive scenes on the same part of the music are one section of it.
    sections = []
    for scene in scenes:
        kind = scene["music"]
        if kind != "module" and sections and sections[-1]["kind"] == kind:
            sections[-1]["bars"] += scene["bars"]
            continue
        sections.append(
            {
                "kind": kind,
                "key": scene["id"],
                "start": scene["start"],
                "bars": scene["bars"],
            }
        )
    for section in sections:
        section["drumBars"] = {
            "intro": 0,
            "module": section["bars"],
            "platform": 0,
            "outro": 2,
        }[section["kind"]]

    return {
        "bpm": BPM,
        "beatsPerBar": BEATS_PER_BAR,
        "duration": cursor * bar,
        "scenes": scenes,
        "sections": sections,
    }


def ensure_takes():
    missing = [name for name in TAKE_NAMES if not (TAKES / f"{name}-0.mp4").is_file()]
    if not missing:
        return
    print(f"Missing takes {missing}: filming them with the presentation pipeline...")
    from scripts.presentation import capture
    from scripts.presentation.scenes import PLATFORM
    from scripts.presentation_video import resolve_scenes

    with capture.demo() as (base_url, context):
        capture.capture_all(
            resolve_scenes(),
            PLATFORM,
            TAKES.parent,
            base_url,
            context,
            only=set(missing),
        )


def write_project(out_dir: Path, fmt, plan, audio: Path):
    from scripts.presentation.composition import VENDOR

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "compositions").mkdir(parents=True)
    for name, src in VENDOR.items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    shutil.copytree(TAKES, out_dir / "assets" / "clips")
    shutil.copy2(audio, out_dir / "assets" / audio.name)
    for name in ("promo.css", "promo.js", "hyperframes.json"):
        shutil.copy2(SOURCE / name, out_dir / name)

    width, height = fmt["width"], fmt["height"]
    for scene in plan["scenes"]:
        html = (SOURCE / "compositions" / f"{scene['id']}.html").read_text(
            encoding="utf-8"
        )
        # The scenes are authored at 1920x1080; their CSS switches layout on
        # orientation, only the declared canvas changes per format.
        html = html.replace(
            'data-width="1920" data-height="1080"',
            f'data-width="{width}" data-height="{height}"',
        )
        (out_dir / "compositions" / f"{scene['id']}.html").write_text(
            html, encoding="utf-8"
        )

    (out_dir / "index.html").write_text(
        render_index(fmt, plan, audio.name), encoding="utf-8"
    )


def render_index(fmt, plan, audio_name):
    width, height = fmt["width"], fmt["height"]
    duration = plan["duration"]
    slots = "\n    ".join(
        f'<div id="{s["id"]}" data-composition-id="{s["id"]}" '
        f'data-composition-src="compositions/{s["id"]}.html" '
        f'data-start="{s["slot_start"]:.3f}" '
        f'data-duration="{s["duration"] + s["lead"] + (OVERLAP if i + 1 < len(plan["scenes"]) else 0):.3f}" '
        f'data-track-index="{1 + i % 2}" data-width="{width}" data-height="{height}"></div>'
        for i, s in enumerate(plan["scenes"])
    )
    data = {
        "bpm": plan["bpm"],
        "duration": duration,
        "orientation": "portrait" if height > width else "landscape",
        "scenes": [
            {k: s[k] for k in ("id", "start", "duration", "lead", "cut")}
            for s in plan["scenes"]
        ],
        "sections": plan["sections"],
    }
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width={width}, height={height}">
  <title>Workspace - promo</title>
  <link rel="stylesheet" href="promo.css">
  <script src="vendor/gsap.min.js"></script>
  <script src="vendor/lucide.js"></script>
  <script>
    window.PROMO = {json.dumps(data)};
  </script>
  <script src="promo.js"></script>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.3f}" data-width="{width}" data-height="{height}">
    <div id="backdrop" class="layer">
      <div class="glow a"></div><div class="glow b"></div>
      <div class="ghost" data-layout-ignore>WORKSPACE</div>
      <div class="layer grid"></div>
    </div>
    {slots}
    <div id="cover" class="layer"><div class="block a"></div><div class="block b"></div><div class="block c"></div></div>
    <div id="flash" class="layer"></div>
    <div class="layer grain"></div>
    <div id="fade" class="layer"></div>
    <audio id="soundtrack" src="assets/{audio_name}" data-start="0" data-duration="{duration:.3f}" data-track-index="10" data-volume="1"></audio>
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    window.promo.stage(tl);
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--aspect", choices=[*FORMATS, "both"], default="both")
    parser.add_argument(
        "--preview", action="store_true", help="open HyperFrames Studio"
    )
    parser.add_argument("--stills", nargs="+", type=float, metavar="SECONDS")
    parser.add_argument(
        "--draft", action="store_true", help="fast, lower-quality render"
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    os.chdir(REPO_ROOT)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")

    from scripts.presentation.soundtrack import render_soundtrack
    from scripts.presentation_video import hyperframes

    ensure_takes()
    plan = timeline()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / "promo-soundtrack.wav"
    print(f"Synthesizing the soundtrack ({plan['duration']:.1f}s at {BPM} BPM)...")
    render_soundtrack(plan, audio)

    aspects = list(FORMATS) if args.aspect == "both" else [args.aspect]
    if args.preview:
        aspects = aspects[:1]
    for aspect in aspects:
        fmt = FORMATS[aspect]
        project = out_dir / aspect
        print(f"Writing the {aspect} composition to {project}/ ...")
        write_project(project, fmt, plan, audio)
        if args.preview:
            hyperframes("preview", str(project))
            return
        if args.stills:
            at = ",".join(f"{t:g}" for t in args.stills)
            hyperframes("snapshot", str(project), "--at", at)
            continue
        if hyperframes("check", str(project)).returncode:
            sys.exit(f"The {aspect} composition does not pass `hyperframes check`.")
        output = out_dir / f"workspace-promo-{fmt['suffix']}.mp4"
        print(f"Rendering {output} ...")
        result = hyperframes(
            "render", str(project), "--output", str(output), "--fps", "30",
            "--quality", "draft" if args.draft else "delivery",
        )  # fmt: skip
        if result.returncode:
            sys.exit(f"The {aspect} render failed.")
        print(f"Done. {output}")


if __name__ == "__main__":
    main()
