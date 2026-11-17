"""Regenerate the README / docs screenshots automatically.

Boots the app against a throwaway SQLite database, seeds deterministic
demo data, then drives a headless Chromium (Playwright) through every
page listed in ``SHOTS`` and writes the captures to ``docs/images/``.

Run it before a release so the screenshots track the current UI:

    uv run python scripts/screenshots.py              # all screenshots
    uv run python scripts/screenshots.py --only files_1 projects_1
    uv run python scripts/screenshots.py --list

Requirements: the ``dev`` dependency group (Playwright) and a Chromium
install (``uv run playwright install chromium``, or set
``SCREENSHOTS_CHROMIUM`` to an existing Chromium binary). The first run
downloads, into the photo datasets' cache: the demo photo library from Open
Images (about 15 MB), the photos of the People shots from the face bench's
dataset, and the face detection weights (about 40 MB). The Map shots fetch
tiles from OpenStreetMap unless ``PHOTOS_MAP_TILE_URL`` names another server.
The photos' credits are rewritten to ``docs/images/CREDITS.md``.
"""

import argparse
import contextlib
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "images"
CREDITS_PATH = OUTPUT_DIR / "CREDITS.md"

VIEWPORT = {"width": 1280, "height": 900}
USERNAME = "alex"
PASSWORD = "screenshots-demo"

# The OpenStreetMap tile server: its usage policy allows a few hundred tiles
# fetched by a browser, which is what one capture of the Map page costs.
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_ATTRIBUTION = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    "contributors"
)

# Pinned, not inherited from the host: pages format dates client-side with
# toLocaleDateString(undefined, ...), so an unpinned context captures the
# machine's locale (a French host writes "samedi 25 juillet"). The timezone
# matches settings.TIME_ZONE so client-rendered times agree with server ones.
CONTEXT_OPTIONS = {
    "viewport": VIEWPORT,
    "locale": "en-US",
    "timezone_id": "UTC",
}


def _fetch(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def demo_environment():
    """Temp DB + media root, migrated and seeded, with a running server."""
    from scripts import photo_dataset

    tmp = Path(tempfile.mkdtemp(prefix="workspace-screenshots-"))
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp / 'db.sqlite3'}",
        "MEDIA_ROOT": str(tmp / "media"),
        "DEBUG": "True",
        "REDIS_URL": "",
        # The vault is a preview, offered to staff only by default.
        "PREVIEW_VISIBILITY": "all",
        # The People shots: faces are found while the seed uploads the photos,
        # with weights kept next to the photo datasets between runs.
        "PHOTOS_FACES_ENABLED": "1",
        "PHOTOS_MODEL_DIR": os.environ.get("PHOTOS_MODEL_DIR")
        or str(photo_dataset.default_cache_dir().parent / "models"),
        # The Map shot, on the instance's own tile server when it names one.
        "PHOTOS_MAP_TILE_URL": os.environ.get("PHOTOS_MAP_TILE_URL") or MAP_TILE_URL,
        "PHOTOS_MAP_TILE_ATTRIBUTION": os.environ.get("PHOTOS_MAP_TILE_ATTRIBUTION")
        or MAP_TILE_ATTRIBUTION,
    }
    os.environ.update(env)

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
    django.setup()
    # DEBUG turns on per-query SQL logging - far too noisy for seeding.
    logging.getLogger("django.db.backends").setLevel(logging.INFO)
    from django.core.management import call_command

    print("Migrating throwaway database...")
    call_command("migrate", verbosity=0)
    print("Seeding demo data...")
    from scripts._screenshot_seed import seed

    context = seed(username=USERNAME, password=PASSWORD)

    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                _fetch(f"{base_url}/health/live", timeout=2)
                break
            except OSError as exc:
                if server.poll() is not None:
                    raise RuntimeError("dev server exited during startup") from exc
                time.sleep(0.5)
        else:
            raise RuntimeError("dev server did not come up")
        yield base_url, context
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


def chromium_path():
    explicit = os.environ.get("SCREENSHOTS_CHROMIUM")
    if explicit:
        return explicit
    default = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")) / "chromium"
    if default.is_file():
        return str(default)
    return None  # let Playwright resolve its own managed install


def capture(base_url, context, only=None):
    from playwright.sync_api import sync_playwright

    from scripts._screenshot_seed import SHOTS

    shots = [s for s in SHOTS if only is None or s["name"] in only]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path())
        ctx = browser.new_context(**CONTEXT_OPTIONS)
        page = ctx.new_page()

        page.goto(f"{base_url}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("load")
        page.wait_for_timeout(1500)
        _dismiss_overlays(page)

        theme = "light"
        for shot in shots:
            print(f"  {shot['name']}.png  <-  {shot['path']}")
            if shot.get("theme", "light") != theme:
                theme = shot.get("theme", "light")
                _set_theme(page, theme)
            page.goto(base_url + shot["path"].format(**context))
            page.wait_for_load_state("load")
            page.wait_for_timeout(shot.get("settle_ms", 2000))
            _dismiss_overlays(page)
            if "prep" in shot:
                values = {key: context[key] for key in shot.get("prep_context", ())}
                shot["prep"](page, **values)
            page.screenshot(path=OUTPUT_DIR / f"{shot['name']}.png")
        if theme != "light":
            _set_theme(page, "light")
        compose_showcase(page)
        browser.close()


def _set_theme(page, theme):
    # Through the settings API, from the page: the server caches the setting,
    # so writing it from this process would not reach the running server.
    page.evaluate(
        """theme => fetch('/api/v1/settings/core', {
               method: 'PATCH',
               headers: {'Content-Type': 'application/json',
                         'X-CSRFToken': getCSRFToken()},
               body: JSON.stringify({theme}),
           })""",
        theme,
    )


# The README's banner: three captures as browser windows over the app's
# gradient, the light dashboard in front of two dark pages. Rendered by the
# same browser from a page next to the captures, so it follows them.
SHOWCASE = {
    "name": "showcase",
    "size": {"width": 1600, "height": 820},
    "back_left": "photos_dark",
    "front": "home",
    "back_right": "chat_dark",
}

SHOWCASE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; }}
  body {{
    background:
      radial-gradient(circle at 15% 20%, rgba(255,255,255,.35), transparent 40%),
      linear-gradient(135deg, #6366f1 0%, #8b5cf6 45%, #ec4899 100%);
    perspective: 2200px;
  }}
  .window {{
    position: absolute; width: 900px; border-radius: 14px; overflow: hidden;
    background: #fff; box-shadow: 0 40px 80px -20px rgba(15, 23, 42, .55),
    0 0 0 1px rgba(15, 23, 42, .08);
  }}
  .window.dark {{ background: #1d232a; }}
  .bar {{ height: 28px; display: flex; align-items: center; gap: 7px;
          padding: 0 12px; background: #f1f2f6; }}
  .dark .bar {{ background: #2a303c; }}
  .bar i {{ width: 11px; height: 11px; border-radius: 50%; display: block; }}
  .bar i:nth-child(1) {{ background: #ff5f57; }}
  .bar i:nth-child(2) {{ background: #febc2e; }}
  .bar i:nth-child(3) {{ background: #28c840; }}
  .window img {{ display: block; width: 100%; }}
  .left  {{ left: 40px;  top: 150px; transform: rotateY(18deg) scale(.82); transform-origin: left center; }}
  .right {{ right: 40px; top: 150px; transform: rotateY(-18deg) scale(.82); transform-origin: right center; }}
  .front {{ left: 350px; top: 60px; width: 900px; z-index: 2; }}
</style></head><body>
  <div class="window dark left"><div class="bar"><i></i><i></i><i></i></div><img src="{back_left}.png"></div>
  <div class="window dark right"><div class="bar"><i></i><i></i><i></i></div><img src="{back_right}.png"></div>
  <div class="window front"><div class="bar"><i></i><i></i><i></i></div><img src="{front}.png"></div>
</body></html>"""


def compose_showcase(page):
    """Render docs/images/showcase.png from the captures it names."""
    names = (SHOWCASE["back_left"], SHOWCASE["front"], SHOWCASE["back_right"])
    if not all((OUTPUT_DIR / f"{name}.png").is_file() for name in names):
        return
    print(f"  {SHOWCASE['name']}.png  <-  {', '.join(names)}")
    html = OUTPUT_DIR / ".showcase.html"
    html.write_text(
        SHOWCASE_HTML.format(**SHOWCASE["size"], **SHOWCASE), encoding="utf-8"
    )
    try:
        page.set_viewport_size(SHOWCASE["size"])
        page.goto(html.as_uri())
        page.wait_for_load_state("load")
        page.screenshot(path=OUTPUT_DIR / f"{SHOWCASE['name']}.png")
    finally:
        html.unlink()
        page.set_viewport_size(VIEWPORT)


def write_credits():
    """Name the author of every photo the captures show, as their CC BY
    licence asks of any copy, next to the images in docs/images/."""
    from scripts._screenshot_seed import (
        LIBRARY_LICENSE,
        LIBRARY_LICENSE_URL,
        load_library,
        load_people_library,
    )

    lines = [
        "# Screenshot credits",
        "",
        "The photos in the screenshots come from the Open Images dataset and are",
        f"licensed [{LIBRARY_LICENSE}]({LIBRARY_LICENSE_URL}) by their authors.",
        "They are shown downscaled, with capture dates and places set by the demo",
        "seed. This file is written by `scripts/screenshots.py`.",
        "",
    ]
    for photo in sorted(load_library(), key=lambda p: p["name"]):
        title = photo["title"] or photo["name"]
        lines.append(
            f"- `{photo['name']}`: [{title}]({photo['url']}) "
            f"by [{photo['author']}]({photo['author_url']})"
        )
    people = load_people_library()
    lines += [
        "",
        f"The photos of the People screenshots, in the `Friends` folder, are "
        f"{people[0].author}'s, from the YFCC100M dataset, also licensed "
        f"[{LIBRARY_LICENSE}]({LIBRARY_LICENSE_URL}). Their names are the "
        "demo's, and so are the names given to the people in them:",
        "",
    ]
    for n, photo in enumerate(people, 1):
        lines.append(
            f"- `IMG_{n:04d}.jpg`: [{photo.title or 'Untitled'}]({photo.page_url})"
        )
    lines += [
        "",
        "Map tiles &copy; [OpenStreetMap](https://www.openstreetmap.org/copyright) "
        "contributors.",
    ]
    CREDITS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dismiss_overlays(page):
    # The seed marks onboarding/changelog as seen; closing the dialogs
    # here is just a safety net (a click would be flaky - the buttons
    # exist in the DOM even when the dialogs are closed).
    page.evaluate(
        """for (const id of ['onboarding-dialog', 'changelog-dialog']) {
               document.getElementById(id)?.close?.();
           }"""
    )
    # Debug toolbar is on because the server runs with DEBUG=True.
    page.evaluate("document.getElementById('djDebugRoot')?.remove()")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--only", nargs="+", metavar="NAME", help="capture only these screenshots"
    )
    parser.add_argument(
        "--list", action="store_true", help="list available screenshot names"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))

    if args.list:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
        from scripts._screenshot_seed import SHOTS

        for shot in SHOTS:
            print(f"{shot['name']:14} {shot['path']}")
        return

    with demo_environment() as (base_url, context):
        print(f"Capturing to {OUTPUT_DIR}/ ...")
        capture(base_url, context, only=set(args.only) if args.only else None)
    write_credits()
    print("Done.")


if __name__ == "__main__":
    main()
