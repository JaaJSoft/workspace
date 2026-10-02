"""Render the Workspace promo: a fifty-second, beat-cut ad in 16:9 and 9:16.

Films real interactions in every module on a seeded throwaway demo (a visible
cursor searching, dragging, typing), synthesizes a soundtrack locked to the
cut, and renders one HyperFrames project per format:

    uv run python scripts/promo_video.py --refilm         # film every take, render both formats
    uv run python scripts/promo_video.py                  # reuse the takes already filmed
    uv run python scripts/promo_video.py --aspect portrait
    uv run python scripts/promo_video.py --preview        # HyperFrames Studio (16:9)
    uv run python scripts/promo_video.py --stills 3 12 40 # snapshots only

Every module gets the same beat: a full-bleed title card in its colour, then
its filmed take full screen, then a full-screen cut into the next colour. Those
scenes come from one template (``scripts/promo/feature.html``) and a line each
in ``SCENES``; the hook, the wall, the breakdown, the roll and the close are
hand-written HyperFrames sub-compositions in ``scripts/promo/compositions/``.
This script owns the timing (lengths in bars, cuts, music) and assembles one
project per format under ``build/promo/``. The takes land in
``build/promo/takes``; missing ones are filmed first. See
``scripts/promo/README.md``.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from string import Template

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "scripts" / "promo"
DEFAULT_OUT = REPO_ROOT / "build" / "promo"
TAKES_DIR = DEFAULT_OUT / "takes"
NODE_MODULES = SOURCE / "node_modules"

BPM = 128
BEATS_PER_BAR = 4
# Scenes overlap by this much, so a transition has both sides on screen.
OVERLAP = 0.3

# Copied into each project: the faces ship with it, because the renderer only
# embeds the families it finds written out in a font-family.
VENDOR = {
    "vendor/gsap.min.js": NODE_MODULES / "gsap/dist/gsap.min.js",
    "vendor/lucide.js": REPO_ROOT
    / "workspace/common/static/ui/js/vendor/lucide/lucide.js",
    "fonts/archivo-black-latin-400-normal.woff2": NODE_MODULES
    / "@fontsource/archivo-black/files/archivo-black-latin-400-normal.woff2",
    "fonts/jetbrains-mono-latin-400-normal.woff2": NODE_MODULES
    / "@fontsource/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2",
    "fonts/jetbrains-mono-latin-600-normal.woff2": NODE_MODULES
    / "@fontsource/jetbrains-mono/files/jetbrains-mono-latin-600-normal.woff2",
}

FORMATS = {
    "landscape": {"width": 1920, "height": 1080, "suffix": "16x9"},
    "portrait": {"width": 1080, "height": 1920, "suffix": "9x16"},
}

# In playing order: the sub-composition, its length in bars, the full-screen
# cut into it, and the part of the music it sits on (see soundtrack.py:
# "module" plays the full groove, "platform" is the breakdown ending on a
# riser, "roll" builds with a clap on each of its `hits` (beats from its
# start), "outro" drops back in and rings out). A scene with a `feature` is
# built from the feature template: its title card lasts `card` beats (0: no
# card, the take is on screen from the cut), then its take plays full screen
# from `media` seconds at `rate` times its speed.
SCENES = [
    {"id": "open", "bars": 1, "cut": None, "music": "module", "color": "#4338ca"},
    {"id": "rush", "bars": 1, "cut": "hard", "music": "roll", "color": "#047857",
     "hits": [0, 0.5, 1, 1.5, 2, 2.5, 3]},
    {"id": "files", "bars": 2, "cut": "flash", "music": "module", "color": "#4338ca",
     "feature": {"icon": "hard-drive", "title": ["Every file,", "every photo."], "take": "files", "media": 1.1, "rate": 1.5, "card": 0}},
    {"id": "projects", "bars": 2, "cut": "push-up", "music": "module", "color": "#c2410c",
     "feature": {"icon": "square-kanban", "title": ["Ship", "your projects."], "take": "projects", "media": 0.5, "rate": 1.5, "card": 2}},
    {"id": "chat", "bars": 2, "cut": "blocks", "music": "module", "color": "#0369a1",
     "feature": {"icon": "message-circle", "title": ["Chat", "in real time."], "take": "chat", "media": 0.9, "rate": 1.7, "card": 2}},
    {"id": "ai", "bars": 2, "cut": "split", "music": "module", "color": "#be185d",
     "feature": {"icon": "sparkles", "title": ["Ask", "your AI."], "take": "ai", "media": 1.2, "rate": 1.9, "card": 2}},
    {"id": "notes", "bars": 1, "cut": "iris", "music": "module", "color": "#047857",
     "feature": {"icon": "notebook-pen", "title": ["Write."], "take": "notes", "media": 3.4, "rate": 1.7, "card": 1}},
    {"id": "mail", "bars": 1, "cut": "push-left", "music": "module", "color": "#b45309",
     "feature": {"icon": "mail", "title": ["Mail."], "take": "mail", "media": 4.4, "rate": 1.5, "card": 1}},
    {"id": "calendar", "bars": 1, "cut": "push-up", "music": "module", "color": "#0f766e",
     "feature": {"icon": "calendar", "title": ["Plan."], "take": "calendar", "media": 1.3, "rate": 1.8, "card": 1}},
    {"id": "people", "bars": 1, "cut": "push-left", "music": "module", "color": "#be123c",
     "feature": {"icon": "contact", "title": ["Contacts."], "take": "people", "media": 1.3, "rate": 1.7, "card": 1}},
    {"id": "vault", "bars": 1, "cut": "split", "music": "module", "color": "#7e22ce",
     "feature": {"icon": "key-round", "title": ["Passwords."], "take": "vault", "media": 2.6, "rate": 1.6, "card": 1}},
    {"id": "search", "bars": 2, "cut": "iris", "music": "module", "color": "#a21caf",
     "feature": {"icon": "search", "title": ["Find", "anything."], "take": "dashboard", "media": 1.1, "rate": 1.3, "card": 2}},
    {"id": "wall", "bars": 2, "cut": "blocks", "music": "module", "color": "#6d28d9"},
    {"id": "yours", "bars": 2, "cut": "push-up", "music": "platform", "color": "#1e1b4b"},
    {"id": "roll", "bars": 2, "cut": "hard", "music": "roll", "color": "#4338ca",
     "hits": [0, 1, 2, 3, 4, 4.5, 5, 5.5, 6, 6.25, 6.5, 6.75, 7, 7.25, 7.5, 7.75]},
    {"id": "cta", "bars": 3, "cut": "flash", "music": "outro", "color": "#6d28d9"},
]  # fmt: skip


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
                **({"hits": scene["hits"]} if "hits" in scene else {}),
            }
        )
    for section in sections:
        section["drumBars"] = {
            "module": section["bars"],
            "platform": 0,
            "roll": 0,
            "outro": 2,
        }[section["kind"]]

    return {
        "bpm": BPM,
        "beatsPerBar": BEATS_PER_BAR,
        "duration": cursor * bar,
        "scenes": scenes,
        "sections": sections,
    }


def takes_to_film(refilm):
    """The takes *refilm* names (every take when empty), plus any not filmed yet."""
    from scripts.promo.takes import TAKES

    wanted = list(TAKES) if refilm == [] else list(refilm or [])
    missing = [name for name in TAKES if not (TAKES_DIR / f"{name}.mp4").is_file()]
    return list(dict.fromkeys([*wanted, *missing]))


def film(names):
    """Film these takes on a freshly seeded demo."""
    from scripts.promo import capture

    with capture.demo() as (base_url, context):
        capture.film(names, TAKES_DIR, base_url, context)


def ensure_toolchain():
    """Install the pinned render toolchain on first use."""
    npm = shutil.which("npm")
    if not npm or not shutil.which("npx"):
        sys.exit("Node.js 22+ is required (npm and npx on the PATH).")
    if not (NODE_MODULES / "hyperframes").is_dir():
        print("Installing the HyperFrames toolchain...")
        subprocess.run([npm, "ci", "--no-fund", "--no-audit"], cwd=SOURCE, check=True)


def hyperframes(*args):
    """Run the pinned HyperFrames CLI."""
    ensure_toolchain()
    env = {
        **os.environ,
        # A repository script has no business reporting usage anywhere.
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "HYPERFRAMES_SKIP_SKILLS": "1",
    }
    return subprocess.run(
        [shutil.which("npx"), "--no-install", "hyperframes", *args],
        cwd=SOURCE,
        env=env,
    )


def write_project(out_dir: Path, fmt, plan, audio: Path):
    # GSAP and the fonts are copied out of the toolchain.
    ensure_toolchain()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "compositions").mkdir(parents=True)
    for name, src in VENDOR.items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    shutil.copytree(TAKES_DIR, out_dir / "assets" / "takes")
    shutil.copy2(audio, out_dir / "assets" / audio.name)
    for name in ("promo.css", "promo.js", "hyperframes.json"):
        shutil.copy2(SOURCE / name, out_dir / name)

    width, height = fmt["width"], fmt["height"]
    for scene in plan["scenes"]:
        if "feature" in scene:
            html = render_feature(scene)
        else:
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


def render_feature(scene):
    """A module's scene: its title card, then its take full screen."""
    feature = scene["feature"]
    beat = 60 / BPM
    clip_at = scene["lead"] + feature["card"] * beat
    # The take plays until the slot closes, through the cut out of it.
    clip_duration = scene["lead"] + scene["duration"] + OVERLAP - clip_at
    needed = feature["media"] + clip_duration * feature["rate"]
    length = take_length(feature["take"])
    if length is not None and needed > length:
        print(
            f"  ! {scene['id']}: the take ends at {length:.1f}s, the scene reads it "
            f"up to {needed:.1f}s: lower its media start or raise its rate"
        )
    title = feature["title"]
    lines = "".join(
        f'<div class="ln">{"<em>" + line + "</em>" if i else line}</div>'
        for i, line in enumerate(title)
    )
    return Template((SOURCE / "feature.html").read_text(encoding="utf-8")).substitute(
        id=scene["id"],
        color=scene["color"],
        icon=feature["icon"],
        lines=lines,
        label=" ".join(title),
        take=feature["take"],
        clip_at=f"{clip_at:.3f}",
        clip_duration=f"{clip_duration:.3f}",
        media=feature["media"],
        rate=feature["rate"],
        card=feature["card"],
    )


def take_length(name):
    """The take's length in seconds, or None when it is not filmed yet."""
    take = TAKES_DIR / f"{name}.mp4"
    if not take.is_file():
        return None
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(take)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return float(out.stdout.strip())


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
            {
                k: s[k]
                for k in ("id", "start", "duration", "lead", "cut", "color", "hits")
                if k in s
            }
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
    sys.path.insert(0, str(REPO_ROOT))
    os.chdir(REPO_ROOT)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")

    from scripts.promo.soundtrack import render_soundtrack
    from scripts.promo.takes import TAKES

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--aspect", choices=[*FORMATS, "both"], default="both")
    parser.add_argument(
        "--preview", action="store_true", help="open HyperFrames Studio"
    )
    parser.add_argument("--stills", nargs="+", type=float, metavar="SECONDS")
    parser.add_argument(
        "--draft", action="store_true", help="fast, lower-quality render"
    )
    parser.add_argument(
        "--refilm",
        nargs="*",
        choices=list(TAKES),
        metavar="TAKE",
        help="film these takes again on a fresh demo (every take when none is named)",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    names = takes_to_film(args.refilm)
    if names:
        print(f"Filming {names} on a fresh demo...")
        film(names)
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
            sys.exit(hyperframes("preview", str(project)).returncode)
        if args.stills:
            at = ",".join(f"{t:g}" for t in args.stills)
            if hyperframes("snapshot", str(project), "--at", at).returncode:
                sys.exit(f"The {aspect} snapshots failed.")
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
