"""Screenshots of the seeded demo, one per shot of every scene.

Reuses the throwaway environment of ``scripts/screenshots.py`` (temp SQLite
database, deterministic seed, dev server on a free port) and adds what the
README captures leave out: preview modules and a seeded password vault.
"""

import os
from pathlib import Path

from scripts.screenshots import (
    PASSWORD,
    USERNAME,
    _dismiss_overlays,
    chromium_path,
    demo_environment,
)

from .scenes import CAPTURE_VIEWPORT

# Twice the CSS pixels, so the camera can push in without going soft.
DEVICE_SCALE_FACTOR = 2


def shot_filename(key, index):
    return f"{key}-{index}.png"


def capture_all(resolve_scenes, platform, shots_dir: Path):
    """Seed a throwaway demo and write every shot under *shots_dir*.

    *resolve_scenes* is called once Django is set up (the scenes read the
    module registry); the scenes it returns are returned.
    """
    # Preview modules (photos, vault) are hidden from non-staff users.
    os.environ["PREVIEW_VISIBILITY"] = "all"
    with demo_environment() as (base_url, context):
        scenes = resolve_scenes()
        from django.contrib.auth.models import User

        from scripts.seed_vault import MASTER_PASSWORD, seed_vault_for

        summary = seed_vault_for(User.objects.get(username=USERNAME))
        context = {
            **context,
            "vault_password": MASTER_PASSWORD,
            "vault_recovery_key": summary["recovery_key"],
        }
        _capture(base_url, context, scenes, platform, shots_dir)
    return scenes


def _capture(base_url, context, scenes, platform, shots_dir):
    from playwright.sync_api import sync_playwright

    shots_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        (scene["key"], index, shot)
        for scene in scenes
        for index, shot in enumerate(scene["shots"])
    ]
    jobs += [("platform", name, shot) for name, shot in platform["shots"].items()]

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path())
        ctx = browser.new_context(
            viewport=CAPTURE_VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="en-US",
            timezone_id="UTC",
        )
        page = ctx.new_page()
        page.goto(f"{base_url}/login")
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("load")
        page.wait_for_timeout(1500)

        for key, index, shot in jobs:
            name = shot_filename(key, index)
            print(f"  {name:22} <-  {shot['path']}")
            _set_theme(page, shot.get("theme", "light"))
            page.goto(base_url + shot["path"].format(**context))
            page.wait_for_load_state("load")
            page.wait_for_timeout(shot.get("settle_ms", 2000))
            _dismiss_overlays(page)
            if "prep" in shot:
                prep = shot["prep"]
                if prep.__code__.co_argcount == 2:
                    prep(page, context)
                else:
                    prep(page)
            _dismiss_overlays(page)
            page.screenshot(path=shots_dir / name)
        _set_theme(page, "light")
        browser.close()


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
