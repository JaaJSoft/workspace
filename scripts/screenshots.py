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
``SCREENSHOTS_CHROMIUM`` to an existing Chromium binary).

By default the browser loads the CDN assets used by base.html (Alpine,
lucide, ...) directly, like any normal page view. ``--offline`` serves
them from a local cache (``scripts/.screenshot-assets/``, filled from
the CDN or from registry.npmjs.org tarballs) for environments without
CDN egress, e.g. a locked-down CI.
"""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_CACHE = Path(__file__).resolve().parent / ".screenshot-assets"
OUTPUT_DIR = REPO_ROOT / "docs" / "images"

VIEWPORT = {"width": 1280, "height": 900}
USERNAME = "alex"
PASSWORD = "screenshots-demo"

# --offline only: npm CDN URLs (jsdelivr / unpkg) are intercepted and
# served from a local cache. On a cache miss the file is fetched from
# the CDN when reachable, else from the registry.npmjs.org tarball (the
# registry often stays reachable where the CDNs are not).
CDN_URL_RE = re.compile(
    r"^https://(?:cdn\.jsdelivr\.net/npm|unpkg\.com)/"
    r"(?P<package>@[^/@]+/[^/@]+|[^/@]+)@(?P<spec>[^/]+)/(?P<path>.+)$"
)

CONTENT_TYPES = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".svg": "image/svg+xml",
}

# Fetched by some pages but irrelevant to a capture — abort instead of
# letting them time out offline.
BLOCKED_URL_PATTERNS = ["https://fav.farm/**"]


def _fetch(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


_NPM_VERSION_CACHE = {}


def _resolve_npm_version(package, spec):
    """Resolve a CDN version spec (1.11.0 / 1 / 3.x.x / 0) to an exact one."""
    if re.fullmatch(r"\d+\.\d+\.\d+", spec):
        return spec
    if (package, spec) in _NPM_VERSION_CACHE:
        return _NPM_VERSION_CACHE[(package, spec)]
    meta = json.loads(_fetch(f"https://registry.npmjs.org/{package}"))
    major = re.match(r"\d+", spec)
    if major is None:
        version = meta["dist-tags"]["latest"]
    else:
        candidates = [
            v
            for v in meta["versions"]
            if v.startswith(f"{major.group()}.") and re.fullmatch(r"[\d.]+", v)
        ]
        version = max(candidates, key=lambda v: tuple(map(int, v.split("."))))
    _NPM_VERSION_CACHE[(package, spec)] = version
    return version


def _npm_fallback(package, spec, path):
    """Fetch one file out of an npm tarball from registry.npmjs.org."""
    version = _resolve_npm_version(package, spec)
    name = package.split("/")[-1]
    tarball = _fetch(f"https://registry.npmjs.org/{package}/-/{name}-{version}.tgz")
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        return tar.extractfile(f"package/{path}").read()


def fetch_cdn_asset(url):
    """Return the bytes for an npm CDN URL, caching on disk."""
    ASSET_CACHE.mkdir(exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix or ".bin"
    target = ASSET_CACHE / (hashlib.sha1(url.encode()).hexdigest() + suffix)
    if target.exists():
        return target.read_bytes()
    match = CDN_URL_RE.match(url)
    try:
        body = _fetch(url)
    except OSError:
        if match is None:
            raise
        body = _npm_fallback(
            match["package"], match["spec"], match["path"].split("?")[0]
        )
    target.write_bytes(body)
    print(f"  cached {url.rsplit('/', 1)[-1]} ({len(body) // 1024} KB)")
    return body


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def demo_environment():
    """Temp DB + media root, migrated and seeded, with a running server."""
    tmp = Path(tempfile.mkdtemp(prefix="workspace-screenshots-"))
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp / 'db.sqlite3'}",
        "MEDIA_ROOT": str(tmp / "media"),
        "DEBUG": "True",
        "REDIS_URL": "",
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


def capture(base_url, context, only=None, offline=False):
    from playwright.sync_api import sync_playwright

    from scripts._screenshot_seed import SHOTS

    shots = [s for s in SHOTS if only is None or s["name"] in only]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path())
        if offline:
            # The service worker would bypass our CDN routes; block it.
            ctx = browser.new_context(viewport=VIEWPORT, service_workers="block")
            ctx.route("https://cdn.jsdelivr.net/**", _serve_cdn_asset)
            ctx.route("https://unpkg.com/**", _serve_cdn_asset)
            for pattern in BLOCKED_URL_PATTERNS:
                ctx.route(pattern, lambda route: route.abort())
        else:
            ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()

        page.goto(f"{base_url}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("load")
        page.wait_for_timeout(1500)
        _dismiss_overlays(page)

        for shot in shots:
            print(f"  {shot['name']}.png  <-  {shot['path']}")
            page.goto(base_url + shot["path"].format(**context))
            page.wait_for_load_state("load")
            page.wait_for_timeout(shot.get("settle_ms", 2000))
            _dismiss_overlays(page)
            if "prep" in shot:
                shot["prep"](page)
            page.screenshot(path=OUTPUT_DIR / f"{shot['name']}.png")
        browser.close()


def _serve_cdn_asset(route):
    url = route.request.url
    try:
        body = fetch_cdn_asset(url)
    except Exception as exc:  # noqa: BLE001 - a missing asset must not kill the run
        print(f"  ! could not fetch {url}: {exc}")
        route.abort()
        return
    content_type = CONTENT_TYPES.get(
        Path(url.split("?")[0]).suffix, "application/octet-stream"
    )
    route.fulfill(
        status=200,
        headers={
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
        },
        body=body,
    )


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
    parser.add_argument(
        "--offline",
        action="store_true",
        help="serve npm CDN assets from a local cache (registry.npmjs.org "
        "fallback) instead of letting the browser hit the CDNs; for "
        "environments without CDN egress",
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
        capture(
            base_url,
            context,
            only=set(args.only) if args.only else None,
            offline=args.offline,
        )
    print("Done.")


if __name__ == "__main__":
    main()
