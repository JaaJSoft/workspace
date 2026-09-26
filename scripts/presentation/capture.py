"""Film and screenshot the seeded demo, one clip or still per shot.

Reuses the throwaway environment of ``scripts/screenshots.py`` (temp SQLite
database, deterministic seed, dev server on a free port) and adds what the
README captures leave out: preview modules and a seeded password vault.

A shot with an ``act`` is filmed (``recording.Take``) while the act drives the
page; a shot without one is a still.
"""

import contextlib
import json
import os
import time
import urllib.request
from pathlib import Path

from scripts.screenshots import (
    PASSWORD,
    USERNAME,
    _dismiss_overlays,
    chromium_path,
    demo_environment,
)

from .ai_stub import ai_stub
from .recording import CURSOR_SCRIPT, Take, park
from .scenes import CAPTURE_VIEWPORT

# Stills at twice the CSS pixels stay sharp wherever they are scaled.
DEVICE_SCALE_FACTOR = 2


def shot_filename(key, index, shot=None):
    if shot is not None and "act" in shot:
        return f"clips/{key}-{index}.mp4"
    return f"shots/{key}-{index}.png"


@contextlib.contextmanager
def demo():
    """The seeded demo, running: yields its base URL and URL context."""
    # Preview modules (photos, vault) are hidden from non-staff users.
    os.environ["PREVIEW_VISIBILITY"] = "all"
    with ai_stub() as ai_url:
        # The dev server inherits the environment: its assistant answers
        # from the stub, the same scripted reply on every run.
        os.environ.update(
            {"AI_BASE_URL": ai_url, "AI_API_KEY": "demo", "AI_MODEL": "demo"}
        )
        with demo_environment() as (base_url, context):
            from django.contrib.auth.models import User

            from scripts.seed_vault import MASTER_PASSWORD, seed_vault_for

            summary = seed_vault_for(User.objects.get(username=USERNAME))
            yield (
                base_url,
                {
                    **context,
                    "vault_password": MASTER_PASSWORD,
                    "vault_recovery_key": summary["recovery_key"],
                },
            )


def serve(out_dir: Path):
    """Keep a seeded demo up for ``--reuse-demo`` runs, until interrupted."""
    marker = out_dir / "demo.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    with demo() as (base_url, context):
        marker.write_text(json.dumps({"base_url": base_url, "context": context}))
        print(
            f"Demo running at {base_url} (login {USERNAME} / {PASSWORD}). Ctrl+C to stop."
        )
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            marker.unlink(missing_ok=True)


def running_demo(out_dir: Path):
    """The demo a ``--serve`` run left up, or None."""
    marker = out_dir / "demo.json"
    if not marker.is_file():
        return None
    data = json.loads(marker.read_text())
    try:
        urllib.request.urlopen(f"{data['base_url']}/health/live", timeout=3)
    except OSError:
        return None
    return data["base_url"], data["context"]


def capture_all(scenes, platform, out_dir: Path, base_url, context, only=None):
    """Write every shot of *scenes* (or of the scene keys in *only*)."""
    from playwright.sync_api import sync_playwright

    jobs = [
        (scene["key"], index, shot)
        for scene in scenes
        if only is None or scene["key"] in only
        for index, shot in enumerate(scene["shots"])
    ]
    if only is None or "platform" in only:
        jobs += [("platform", name, shot) for name, shot in platform["shots"].items()]

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path())
        ctx = browser.new_context(
            viewport=CAPTURE_VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US",
            timezone_id="UTC",
        )
        ctx.add_init_script(CURSOR_SCRIPT)
        # Copy buttons are filmed too, and headless Chromium refuses them otherwise.
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
        page = ctx.new_page()
        page.goto(f"{base_url}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("load")
        page.wait_for_timeout(1500)

        for key, index, shot in jobs:
            name = shot_filename(key, index, shot)
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"  {name:26} <-  {shot['path']}")
            _set_theme(page, shot.get("theme", "light"))
            page.goto(base_url + shot["path"].format(**context))
            page.wait_for_load_state("load")
            page.wait_for_timeout(shot.get("settle_ms", 2000))
            _dismiss_overlays(page)
            if "prep" in shot:
                _call(shot["prep"], page, context)
            _dismiss_overlays(page)
            if "act" in shot:
                park(page, *shot.get("cursor_from", ()))
                page.wait_for_timeout(300)
                with Take(page, target):
                    _call(shot["act"], page, context)
            else:
                park(page, -50, -50)
                page.screenshot(path=target)
        _set_theme(page, "light")
        browser.close()


def _call(fn, page, context):
    if fn.__code__.co_argcount == 2:
        return fn(page, context)
    return fn(page)


def _set_theme(page, theme):
    # Through the API from the browser session: a server-side write would
    # leave the dev server's own settings cache stale.
    page.evaluate(
        """async theme => {
            const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
            await fetch('/api/v1/settings/core/theme', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
                body: JSON.stringify({value: theme}),
            });
        }""",
        theme,
    )
