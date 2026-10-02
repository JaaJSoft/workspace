"""Film the promo's takes on a seeded throwaway demo.

Reuses the throwaway environment of ``scripts/screenshots.py`` (temp SQLite
database, deterministic seed, dev server on a free port) and adds what the
README captures leave out: preview modules and a seeded password vault. Each
take of ``takes.TAKES`` is filmed (``recording.Take``) while its act drives
the page.
"""

import contextlib
import os
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
from .takes import CAPTURE_VIEWPORT, TAKES

# Takes at twice the CSS pixels stay sharp once scaled into the frame.
DEVICE_SCALE_FACTOR = 2


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


def film(names, out_dir: Path, base_url, context):
    """Film the takes *names* into ``out_dir/<name>.mp4``."""
    from playwright.sync_api import sync_playwright

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
        _set_theme(page, "light")

        out_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            take = TAKES[name]
            print(f"  {name:10} <-  {take['path']}")
            page.goto(base_url + take["path"].format(**context))
            page.wait_for_load_state("load")
            page.wait_for_timeout(take.get("settle_ms", 2000))
            _dismiss_overlays(page)
            if "prep" in take:
                _call(take["prep"], page, context)
            _dismiss_overlays(page)
            park(page, *take.get("cursor_from", ()))
            page.wait_for_timeout(300)
            with Take(page, out_dir / f"{name}.mp4"):
                _call(take["act"], page, context)
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
