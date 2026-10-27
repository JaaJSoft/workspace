"""Film real interactions in the app for the promo video.

A take records the page through Chrome's screencast (every repaint, with its
timestamp), then ffmpeg turns the frames into a constant-rate clip. Pages get
a visible cursor with a click ripple, since a headless browser paints none,
and the gestures below move it the way a person would: eased glides, short
pauses before a click, typing at a human pace.
"""

import base64
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

CURSOR_SCRIPT = r"""
(() => {
  const install = () => {
    if (document.getElementById('demo-cursor')) return;
    const style = document.createElement('style');
    style.textContent = `
      #djDebug, #djDebugRoot { display: none !important; }
      #demo-cursor { position: fixed; left: 0; top: 0; z-index: 2147483647; pointer-events: none;
        width: 30px; height: 30px; transform: translate(-200px, -200px);
        filter: drop-shadow(0 4px 10px rgba(0,0,0,.35)); }
      #demo-cursor svg { width: 100%; height: 100%; transition: transform .12s ease-out; transform-origin: 18% 12%; }
      #demo-cursor.down svg { transform: scale(.82); }
      .demo-ripple { position: fixed; z-index: 2147483646; pointer-events: none; width: 44px; height: 44px;
        margin: -22px 0 0 -22px; border-radius: 50%; border: 3px solid rgba(124, 58, 237, .85);
        background: rgba(124, 58, 237, .18); animation: demo-ripple .55s ease-out forwards; }
      @keyframes demo-ripple { from { transform: scale(.3); opacity: 1; } to { transform: scale(1.5); opacity: 0; } }`;
    document.documentElement.appendChild(style);
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4.2 2.6v16.6l4.5-4.1 3 6.5 2.9-1.3-3-6.4 6.3-.3z" fill="#111827" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(cursor);
    const move = (e) => { cursor.style.transform = `translate(${e.clientX - 5}px, ${e.clientY - 3}px)`; };
    for (const type of ['mousemove', 'pointermove', 'dragover', 'drag']) {
      addEventListener(type, (e) => { if (e.clientX || e.clientY) move(e); }, true);
    }
    addEventListener('mousedown', (e) => {
      cursor.classList.add('down');
      const ripple = document.createElement('div');
      ripple.className = 'demo-ripple';
      ripple.style.left = `${e.clientX}px`;
      ripple.style.top = `${e.clientY}px`;
      document.documentElement.appendChild(ripple);
      setTimeout(() => ripple.remove(), 700);
    }, true);
    for (const type of ['mouseup', 'drop', 'dragend']) {
      addEventListener(type, () => cursor.classList.remove('down'), true);
    }
    // Headless Chrome paints no drag image: carry a copy of the dragged
    // element under the cursor, as a desktop browser would.
    let ghost = null;
    let grab = { x: 0, y: 0 };
    addEventListener('dragstart', (e) => {
      const source = e.target.closest?.('[draggable="true"]') || e.target;
      const box = source.getBoundingClientRect();
      grab = { x: e.clientX - box.left, y: e.clientY - box.top };
      ghost = source.cloneNode(true);
      Object.assign(ghost.style, {
        position: 'fixed', left: '0', top: '0', margin: '0', width: `${box.width}px`,
        zIndex: '2147483646', pointerEvents: 'none', opacity: '0.96',
        boxShadow: '0 28px 50px -12px rgba(0,0,0,.38)',
        transform: `translate(${box.left}px, ${box.top}px) rotate(2.5deg)`,
      });
      document.documentElement.appendChild(ghost);
    }, true);
    for (const type of ['drag', 'dragover']) {
      addEventListener(type, (e) => {
        if (ghost && (e.clientX || e.clientY)) {
          ghost.style.transform = `translate(${e.clientX - grab.x}px, ${e.clientY - grab.y}px) rotate(2.5deg)`;
        }
      }, true);
    }
    for (const type of ['drop', 'dragend']) {
      addEventListener(type, () => { ghost?.remove(); ghost = null; }, true);
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
"""


class Take:
    """Record *page* between ``with`` and the end of the block."""

    def __init__(self, page, output: Path, fps=30, tail=0.6):
        self.page = page
        self.output = output
        self.fps = fps
        self.tail = tail
        self.frames = []

    def __enter__(self):
        self.cdp = self.page.context.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 92, "everyNthFrame": 1},
        )
        self.started = time.time()
        return self

    def _frame(self, params):
        self.frames.append((params["metadata"]["timestamp"], params["data"]))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

    def __exit__(self, *exc):
        self.page.wait_for_timeout(self.tail * 1000)
        self.ended = time.time()
        self.cdp.send("Page.stopScreencast")
        self.cdp.detach()
        if exc[0] is None:
            self._encode(self.ended)

    def _encode(self, ended):
        # Chrome only sends a frame when something repaints, so each frame is
        # held until the next one: a variable rate, resampled to a fixed one.
        frames = [(ts, data) for ts, data in self.frames if ts >= self.started - 0.05]
        if not frames:
            raise RuntimeError(
                f"nothing was painted while recording {self.output.name}"
            )
        workdir = Path(tempfile.mkdtemp(prefix="take-"))
        try:
            lines = []
            # The clip starts when the take does, on the first frame painted.
            timeline = [(self.started, frames[0][1]), *frames]
            for i, (ts, data) in enumerate(timeline):
                path = workdir / f"{i:05d}.jpg"
                path.write_bytes(base64.b64decode(data))
                end = timeline[i + 1][0] if i + 1 < len(timeline) else ended
                lines.append(
                    f"file '{path.as_posix()}'\nduration {max(end - ts, 0.001):.4f}"
                )
            lines.append(
                f"file '{(workdir / f'{len(timeline) - 1:05d}.jpg').as_posix()}'"
            )
            listing = workdir / "frames.txt"
            listing.write_text("\n".join(lines), encoding="utf-8")
            self.output.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-vf", f"fps={self.fps},format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "14",
                    # A keyframe a second: the renderer seeks into the takes,
                    # and x264's default (one every 250 frames) freezes them.
                    "-g", str(self.fps), "-keyint_min", str(self.fps),
                    "-movflags", "+faststart", str(self.output),
                ],
                check=True,
            )  # fmt: skip
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# -- gestures ----------------------------------------------------------------

_position = {}


def _ease(t):
    return 0.5 - math.cos(math.pi * t) / 2


def glide(page, x, y, duration=0.7):
    """Move the cursor to (x, y) along an eased, slightly curved path."""
    x0, y0 = _position.get(id(page), (page.viewport_size["width"] / 2, 300))
    bend = min(80, math.hypot(x - x0, y - y0) * 0.12)
    # Paced on the clock, not on a step count: each move costs a round trip
    # to the browser, which would otherwise stretch every glide.
    started = time.monotonic()
    while True:
        progress = min(1.0, (time.monotonic() - started) / duration)
        t = _ease(progress)
        arc = math.sin(math.pi * t) * bend
        page.mouse.move(x0 + (x - x0) * t, y0 + (y - y0) * t - arc)
        if progress >= 1:
            break
        page.wait_for_timeout(8)
    _position[id(page)] = (x, y)


def point(page, target, duration=0.7, dx=0.5, dy=0.5):
    """Glide onto *target* (a locator or selector), at a fraction of its box."""
    locator = page.locator(target) if isinstance(target, str) else target
    box = locator.first.bounding_box()
    if box is None:
        raise RuntimeError(f"nothing to point at: {target}")
    glide(page, box["x"] + box["width"] * dx, box["y"] + box["height"] * dy, duration)
    return box


def click(page, target=None, duration=0.7, pause=0.18, **kwargs):
    if target is not None:
        point(page, target, duration, **kwargs)
    page.wait_for_timeout(pause * 1000)
    page.mouse.down()
    page.wait_for_timeout(90)
    page.mouse.up()


def type_text(page, text, delay=0.06):
    page.keyboard.type(text, delay=delay * 1000)


def drag(page, source, target, duration=1.2, dx=0.5, dy=0.5):
    """Press on *source*, carry it over *target* and let go."""
    point(page, source, 0.7)
    page.wait_for_timeout(200)
    page.mouse.down()
    page.wait_for_timeout(150)
    x, y = _position[id(page)]
    glide(page, x + 12, y + 6, 0.15)
    locator = page.locator(target) if isinstance(target, str) else target
    box = locator.first.bounding_box()
    glide(page, box["x"] + box["width"] * dx, box["y"] + box["height"] * dy, duration)
    page.wait_for_timeout(250)
    page.mouse.up()


def wait(page, seconds):
    page.wait_for_timeout(seconds * 1000)


def park(page, x=None, y=None):
    """Put the cursor somewhere out of the way, without animating."""
    width = page.viewport_size["width"]
    x = width * 0.62 if x is None else x
    y = 520 if y is None else y
    page.mouse.move(x, y)
    _position[id(page)] = (x, y)
