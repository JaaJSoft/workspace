"""What the promo films in each module: one take per entry of ``TAKES``.

Each take is a page of the seeded demo (``scripts/_screenshot_seed.py``) at
``CAPTURE_VIEWPORT``. ``prep`` readies the page off camera, then ``act``
drives it with the gestures of ``recording`` (a visible cursor, typing,
dragging) while it is filmed. ``path`` may name ids of the seeded data in
braces, filled from the demo's context. Keep an act around seven seconds:
the promo plays a few of them, from the moment and at the speed its
``SCENES`` pick.
"""

import time

from .ai_stub import REPLY
from .recording import click, drag, glide, point, type_text, wait

CAPTURE_VIEWPORT = {"width": 1440, "height": 900}


# -- page preparation (off camera) and acts (filmed) ------------------------


def _wait_for_images(page):
    # Thumbnails are lazy images fetched one request each.
    page.wait_for_function(
        "[...document.images].every(img => img.complete && img.naturalWidth > 0)",
        timeout=20000,
    )


def _mosaic(page):
    # The view is a saved preference; the act then opens a folder in it.
    page.evaluate("document.querySelector('[aria-label=\"Mosaic view\"]')?.click()")
    page.wait_for_timeout(800)
    _wait_for_images(page)


def _search(page):
    wait(page, 0.4)
    click(page, "#dashboard-search input", duration=0.9)
    type_text(page, "launch", delay=0.1)
    # The results need the 300 ms debounce and the fetch before the keys work.
    wait(page, 0.9)
    for _ in range(3):
        page.keyboard.press("ArrowDown")
        wait(page, 0.35)
    wait(page, 0.3)
    page.keyboard.press("Enter")
    wait(page, 1.1)


def _browse_photos(page):
    wait(page, 0.3)
    click(page, '[data-node-type="folder"][data-display-name="Photos"]', duration=0.9)
    page.wait_for_selector('[data-display-name="alpine-morning.jpg"]')
    _wait_for_images(page)
    wait(page, 0.2)
    click(page, '[data-display-name="northern-lights.jpg"]', duration=0.7)
    wait(page, 1.1)
    click(page, 'button[title="Next file"]', duration=0.6)
    wait(page, 0.9)


def _photo_viewer(page):
    wait(page, 0.3)
    glide(page, 700, 420, 0.6)
    click(page, '[data-display-name="city-at-night.jpg"]', duration=0.8)
    wait(page, 1.4)
    for _ in range(2):
        page.keyboard.press("ArrowRight")
        wait(page, 0.9)


def _open_note(page):
    page.click("text=Project kickoff")
    page.wait_for_selector(".milkdown .ProseMirror")
    page.wait_for_timeout(1200)


def _write_note(page):
    wait(page, 0.3)
    click(page, ".milkdown .ProseMirror", duration=0.8, dy=0.85)
    page.keyboard.press("Control+End")
    # Markdown shortcuts turn into a heading and a list as they are typed.
    type_text(page, "\n## Next steps\n", delay=0.07)
    type_text(page, "- Ship the beta to the design team\n", delay=0.045)
    type_text(page, "Review on Friday", delay=0.05)
    wait(page, 0.8)


def _send(page, text):
    wait(page, 0.2)
    click(page, 'textarea[x-ref="messageInput"]', duration=0.8)
    type_text(page, text, delay=0.05)
    wait(page, 0.2)
    page.keyboard.press("Enter")


def _chat(page):
    _send(page, "Shipping it today!")
    wait(page, 0.6)
    message = page.locator("[data-message-uuid]").filter(has_text="refresh in a minute")
    point(page, message, 0.5)
    wait(page, 0.2)
    react = (
        page.locator("div.group\\/msg")
        .filter(has=message)
        .locator('button[title="More reactions"]')
    )
    click(page, react, duration=0.4, pause=0.1)
    wait(page, 0.4)
    click(page, "emoji-picker button.emoji >> nth=3", duration=0.4, pause=0.1)
    wait(page, 0.8)


def _ask_assistant(page):
    replies = page.get_by_text(REPLY.splitlines()[0])
    before = replies.count()
    _send(page, "What needs me this week?")
    # Wait for the answer itself: the first one of a fresh server is slower.
    deadline = time.monotonic() + 10
    while replies.count() <= before and time.monotonic() < deadline:
        wait(page, 0.2)
    if replies.count() <= before:
        raise RuntimeError("The assistant did not answer on camera.")
    wait(page, 1.4)


def _reply_to_mail(page):
    wait(page, 0.2)
    click(page, 'div[draggable="true"]:has-text("Re: Launch checklist")', duration=0.8)
    wait(page, 0.8)
    click(page, 'button[title="Reply (R)"]', duration=0.7)
    body = 'textarea[placeholder="Write your message..."]'
    page.wait_for_selector(body)
    click(page, body, duration=0.6, dy=0.12)
    page.keyboard.press("Control+Home")
    type_text(page, "Great news, let's ship on Thursday.", delay=0.045)
    wait(page, 0.7)


def _calendar(page):
    wait(page, 0.2)
    click(page, 'button:text-is("Week")', duration=0.8)
    wait(page, 1.0)
    click(page, ".fc-event:has-text('Sprint planning')", duration=0.8)
    wait(page, 1.4)
    click(page, 'button:text-is("Agenda")', duration=0.7)
    wait(page, 1.0)


def _move_card(page):
    card = 'li.card:has-text("Fix mobile navigation overlap")'
    wait(page, 0.2)
    drag(
        page,
        card,
        'section[data-status-uuid]:has-text("In progress") ul[data-column-list]',
        duration=1.1,
        dy=0.15,
    )
    # The board re-renders after the drop: the card is a new element.
    wait(page, 0.9)
    click(page, card, duration=0.6)
    wait(page, 1.3)


def _find_person(page):
    wait(page, 0.2)
    click(page, 'input[placeholder="Search people..."]', duration=0.8)
    type_text(page, "sam", delay=0.12)
    wait(page, 0.8)
    click(page, "#person-list a[data-person-uuid] >> nth=0", duration=0.7)
    wait(page, 1.4)


def _unlock_vault(page, context):
    page.fill("input[autocomplete='current-password']", context["vault_password"])
    page.fill("input[spellcheck='false']", context["vault_recovery_key"])
    page.click("button:has-text('Unlock')")
    page.get_by_test_id("vault-switcher").wait_for(timeout=60000)
    page.wait_for_timeout(2500)


def _reveal_password(page):
    wait(page, 0.2)
    click(page, 'tr.cursor-pointer:has-text("GitHub")', duration=0.8)
    wait(page, 0.8)
    click(page, 'button[aria-label="Reveal the password"]', duration=0.7)
    wait(page, 1.1)
    click(page, 'button[aria-label="Copy the password"]', duration=0.5)
    wait(page, 1.0)


# -- takes -------------------------------------------------------------------

TAKES = {
    "dashboard": {"path": "/", "act": _search},
    "files": {"path": "/files", "prep": _mosaic, "act": _browse_photos},
    "photos": {"path": "/photos", "prep": _wait_for_images, "act": _photo_viewer},
    "notes": {"path": "/notes", "prep": _open_note, "act": _write_note},
    "chat": {"path": "/chat/{conversation_uuid}", "act": _chat},
    "ai": {"path": "/chat/{bot_conversation_uuid}", "act": _ask_assistant},
    "mail": {"path": "/mail", "act": _reply_to_mail},
    "calendar": {"path": "/calendar", "settle_ms": 3000, "act": _calendar},
    "projects": {"path": "/projects/{project_uuid}/board", "act": _move_card},
    "people": {"path": "/people", "act": _find_person},
    "vault": {
        "path": "/vault",
        "prep": _unlock_vault,
        "settle_ms": 2500,
        "act": _reveal_password,
    },
}
