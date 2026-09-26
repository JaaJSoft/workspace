"""Write the HyperFrames project the video is rendered from.

The markup is generated here, statically, from the timeline: HyperFrames
reads clip timing (``data-start``/``data-duration``) and the soundtrack from
the HTML itself, and its lint and Studio work on that HTML. The motion lives
in ``composition/timeline.js``, one paused GSAP timeline built from the same
timeline data.
"""

import json
import shutil
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "composition"
NODE_MODULES = HERE / "node_modules"
LUCIDE_JS = HERE.parent.parent / "workspace/common/static/ui/js/vendor/lucide/lucide.js"
VENDOR = {
    "vendor/gsap.min.js": NODE_MODULES / "gsap/dist/gsap.min.js",
    "vendor/lucide.js": LUCIDE_JS,
    "fonts/inter-latin-wght-normal.woff2": NODE_MODULES
    / "@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
    "fonts/jetbrains-mono-latin-400-normal.woff2": NODE_MODULES
    / "@fontsource/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2",
    "fonts/jetbrains-mono-latin-600-normal.woff2": NODE_MODULES
    / "@fontsource/jetbrains-mono/files/jetbrains-mono-latin-600-normal.woff2",
}
HOST = "workspace.example.com"
WINDOW_WIDTH = 1060
WINDOW_HEIGHT = 707
WINDOW_TOP = 178


def write_project(out_dir: Path, timeline, audio_name):
    """Lay out *out_dir* as a HyperFrames project (shots/ is already there)."""
    for name, src in VENDOR.items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for src in SOURCE.iterdir():
        shutil.copy2(src, out_dir / src.name)
    (out_dir / "hyperframes.json").write_text(
        json.dumps({"paths": {"assets": "shots"}, "media": {"autoProxy": False}}),
        encoding="utf-8",
    )
    scenes_dir = out_dir / "compositions"
    if scenes_dir.is_dir():
        shutil.rmtree(scenes_dir)
    scenes_dir.mkdir()
    for index, section in enumerate(timeline["sections"]):
        (scenes_dir / f"{section['key']}.html").write_text(
            render_scene(timeline, index), encoding="utf-8"
        )
    (scenes_dir / "hud.html").write_text(render_hud(timeline), encoding="utf-8")
    (scenes_dir / "cuts.html").write_text(render_cuts(timeline), encoding="utf-8")
    (out_dir / "index.html").write_text(
        render_index(timeline, audio_name), encoding="utf-8"
    )


# -- markup helpers ------------------------------------------------------------


def _icon(name):
    return f'<i data-lucide="{escape(name)}"></i>'


def _tile(icon, color=None, cls="", style=""):
    if color:
        style = f"--mc: {color}; {style}"
    attr = f' style="{style.strip()}"' if style else ""
    return f'<div class="tile {cls}"{attr}>{_icon(icon)}</div>'


def _letters(text, masked=True, gradient=True):
    cls = "ch gradient-text" if gradient else "ch"
    chars = "".join(
        f'<span class="{cls}">{"&nbsp;" if c == " " else escape(c)}</span>'
        for c in text
    )
    return f'<span class="line">{chars}</span>' if masked else chars


def _words(text):
    return " ".join(f'<span class="w">{escape(w)}</span>' for w in text.split(" "))


# -- scenes ----------------------------------------------------------------------


def _intro(s, modules):
    gap = 30
    row = len(modules) * 92 + (len(modules) - 1) * gap
    tiles = "".join(
        _tile(
            m["icon"],
            m["color"],
            "orbit-tile",
            f"left: {960 - row / 2 + i * (92 + gap):.0f}px; top: 640px",
        )
        for i, m in enumerate(modules)
    )
    return f"""
<div class="flare"></div>
<div class="intro-exit layer">
  <div class="intro-group layer">
    <div class="hero-logo tile logo-tile" style="top: 276px">{_icon("users")}<div class="ring"></div><div class="ring"></div></div>
    <div class="center-stack hero-title" style="top: 490px">{_letters(s["title"], masked=False)}</div>
    <div class="center-stack hero-tagline" style="top: 700px">{_words(s["tagline"])}</div>
  </div>
</div>
<div class="center-stack hero-sub" style="top: 800px">{_words(s["subline"])}</div>
<div class="layer">{tiles}</div>"""


def _module(s, timeline):
    capture = timeline["capture"]
    right = s["number"] % 2 == 1
    win_x = 760 if right else 100
    text_x = 130 if right else 1180
    scale = WINDOW_WIDTH / capture["width"]
    total = sum(1 for x in timeline["sections"] if x["kind"] == "module")
    badge = '<span class="badge">Preview</span>' if s["preview"] else ""
    features = "".join(
        f'<div class="feat"><div class="feat-ic">{_icon(icon)}<span class="flash"></span></div>'
        f"<span>{escape(label)}</span></div>"
        for icon, label in s["features"]
    )
    paths = "".join(
        f'<span class="p">{"".join(f"<span class=c>{escape(c)}</span>" for c in shot["path"])}</span>'
        for shot in s["shots"]
    )
    images = "".join(f'<img src="{escape(shot["src"])}" alt="">' for shot in s["shots"])

    callouts = []
    for k, shot in enumerate(s["shots"]):
        if not shot.get("callout"):
            continue
        cx, cy, cw, ch = shot["callout"]
        size = scale * min(1.5, 560 / (cw * scale))
        w, h = cw * size, ch * size
        left = win_x - 70 if right else win_x + WINDOW_WIDTH + 70 - w
        top = WINDOW_TOP + WINDOW_HEIGHT - h * 0.55
        callouts.append(
            f'<div class="callout" data-shot="{k}" style="left: {left:.1f}px; top: {top:.1f}px; '
            f"width: {w:.1f}px; height: {h:.1f}px; background-image: url('{escape(shot['src'])}'); "
            f"background-size: {capture['width'] * size:.1f}px {capture['height'] * size:.1f}px; "
            f'background-position: {-cx * size:.1f}px {-cy * size:.1f}px"></div>'
        )

    return f"""
<div class="m-text" style="left: {text_x}px">
  <div class="m-counter"><span>{s["number"]:02d}</span><span class="bar"></span><span class="total">{total:02d}</span>{badge}</div>
  <div class="m-icon">{_tile(s["icon"], s["color"])}<div class="ring"></div></div>
  <div class="m-name">{_letters(s["name"])}</div>
  <div class="m-tagline">{_words(s["tagline"])}</div>
  <div class="m-features">{features}</div>
</div>
<div class="m-glow" style="left: {win_x + 30}px"></div>
<div class="m-window" style="left: {win_x}px">
  <div class="chrome">
    <span class="dot" style="background: #ff5f57"></span><span class="dot" style="background: #febc2e"></span><span class="dot" style="background: #28c840"></span>
    <div class="url">{_icon("lock")}<span>{HOST}</span><span class="path">{paths}</span></div>
    <span class="spacer"></span>
  </div>
  <div class="screen">{images}</div>
</div>
{"".join(callouts)}"""


def _platform(s, modules):
    colors = [m["color"] for m in modules]
    cards = "".join(
        f'<div class="p-card" style="left: {1090 + (i % 2) * 380}px; top: {320 + (i // 2) * 221}px">'
        f"{_tile(icon, colors[(i * 2 + 1) % len(colors)])}"
        f"<h3>{escape(heading)}</h3><p>{escape(body)}</p></div>"
        for i, (icon, heading, body) in enumerate(s["cards"])
    )
    return f"""
<div class="p-inner layer">
  <div class="p-title">{_letters(s["title"])}</div>
  <div class="p-tagline">{_words(s["tagline"])}</div>
  <div class="p-hero">
    <img src="{escape(s["shots"]["light"])}" alt="">
    <img class="dark" src="{escape(s["shots"]["dark"])}" alt="">
    <div class="label light">{_icon("sun")}<span data-layout-allow-overlap>Light theme</span></div>
    <div class="label dark">{_icon("moon")}<span data-layout-allow-overlap>Dark theme</span></div>
  </div>
  {cards}
</div>"""


def _outro(s, modules):
    arms = "".join(
        f'<div class="arm">{_tile(m["icon"], m["color"], "orbit-tile")}</div>'
        for m in modules
    )
    return f"""
<div class="layer">{arms}</div>
<div class="hero-logo tile logo-tile" style="top: 300px">{_icon("users")}<div class="ring"></div><div class="ring"></div><div class="ring"></div></div>
<div class="center-stack hero-title" style="top: 510px">{_letters(s["title"])}</div>
<div class="center-stack hero-tagline" style="top: 720px">{_words(s["tagline"])}</div>
<div class="center-stack url-row" style="top: 830px"><div class="url-pill">{_icon(s["url_icon"])}<span>{escape(s["url"])}</span></div></div>"""


# -- documents -------------------------------------------------------------------


def _subcomposition(timeline, cid, title, body, build):
    """A sub-composition file, everything inside <template>.

    *build* is a JS call on ``tl`` and ``el`` that fills the timeline.
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Workspace - {escape(title)}</title>
</head>
<body>
  <template id="{cid}">
    <div id="{cid}-root" data-composition-id="{cid}" data-width="{timeline["width"]}" data-height="{timeline["height"]}">
      <style>
        #{cid}-root {{ position: absolute; inset: 0; }}
      </style>
      {body}
      <script>
        (() => {{
          // Last child of the root, so the markup above is mounted when it runs.
          // The runtime merges this root into its host slot, which keeps the
          // composition id but not the root's own id.
          const tl = gsap.timeline({{ paused: true }});
          const el = document.querySelector('[data-composition-id="{cid}"]');
          {build};
          window.__timelines["{cid}"] = tl;
        }})();
      </script>
    </div>
  </template>
</body>
</html>
"""


def render_scene(timeline, index):
    """One scene, with its own local timeline."""
    s = timeline["sections"][index]
    builders = {
        "intro": lambda: _intro(s, timeline["modules"]),
        "module": lambda: _module(s, timeline),
        "platform": lambda: _platform(s, timeline["modules"]),
        "outro": lambda: _outro(s, timeline["modules"]),
    }
    body = f'<div class="scene {s["kind"]} layer" style="--mc: {s["color"]}">{builders[s["kind"]]()}</div>'
    return _subcomposition(
        timeline,
        f"scene-{s['key']}",
        s.get("name") or s["kind"],
        body,
        f"window.presentation.scene(tl, el, {index})",
    )


def render_hud(timeline):
    dots = "".join('<div class="hud-dot"></div>' for _ in timeline["modules"])
    body = f"""<div class="hud-inner layer">
        <div class="hud-logo">{_tile("users", cls="logo-tile")}<span>Workspace</span></div>
        <div class="hud-dots">{dots}</div>
      </div>"""
    return _subcomposition(
        timeline, "hud", "progress", body, "window.presentation.hud(tl, el)"
    )


def render_cuts(timeline):
    body = "".join(
        f'<div class="wipe" data-cut="{s["key"]}" style="--mc: {s["color"]}"><div class="band"></div></div>'
        for s in timeline["sections"][1:]
    )
    return _subcomposition(
        timeline, "cuts", "cuts", body, "window.presentation.cuts(tl, el)"
    )


def render_index(timeline, audio_name):
    """The orchestrator: one slot per sub-composition, plus the soundtrack."""
    bar = 60 / timeline["bpm"] * timeline["beatsPerBar"]
    width, height = timeline["width"], timeline["height"]
    duration = timeline["duration"]
    sections = timeline["sections"]
    modules = [s for s in sections if s["kind"] == "module"]

    def slot(cid, src, start, length, track):
        return (
            f'<div id="{cid}" data-composition-id="{cid}" data-composition-src="compositions/{src}.html" '
            f'data-start="{start:.3f}" data-duration="{length:.3f}" data-track-index="{track}" '
            f'data-width="{width}" data-height="{height}"></div>'
        )

    scenes = "\n    ".join(
        slot(f"scene-{s['key']}", s["key"], s["start"], s["bars"] * bar, 1)
        for s in sections
    )
    hud_start = modules[0]["start"]
    hud_end = modules[-1]["start"] + modules[-1]["bars"] * bar
    brand_from, brand_to = timeline["brand"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width={width}, height={height}">
  <title>Workspace - presentation</title>
  <link rel="stylesheet" href="style.css">
  <style>
    :root {{ --brand-from: {brand_from}; --brand-to: {brand_to}; --c: {sections[0]["color"]}; --c2: {brand_to}; }}
  </style>
  <script src="vendor/gsap.min.js"></script>
  <script src="vendor/lucide.js"></script>
  <script>
    window.TIMELINE = {json.dumps(timeline)};
  </script>
  <script src="motion.js"></script>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.3f}" data-width="{width}" data-height="{height}">
    <div id="backdrop" class="layer">
      <div class="blob a"></div><div class="blob b"></div><div class="blob c"></div>
      <div class="floor" data-layout-allow-overflow></div>
      <div class="layer dots"></div>
    </div>
    {scenes}
    {slot("hud", "hud", hud_start, hud_end - hud_start, 2)}
    {slot("cuts", "cuts", 0, duration, 3)}
    <div id="overlays" class="layer">
      <div class="layer grain"></div>
      <div class="layer vignette"></div>
      <div id="fade" class="layer fade"></div>
    </div>
    <audio id="soundtrack" src="{escape(audio_name)}" data-start="0" data-duration="{duration:.3f}" data-track-index="10" data-volume="1"></audio>
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    window.presentation.ambient(tl);
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
"""
