"""What the presentation video says about each module, and what it shows.

One entry per scene, in playing order. A scene whose ``slug`` names a module
of ``core.module_registry`` takes its name, icon and colour from there, so a
rename or a new icon reaches the video on the next render. ``tagline`` and
``features`` are the words on screen; keep them short enough to read in the
four bars a scene lasts.

Each shot is a page of the seeded demo (``scripts/_screenshot_seed.py``) at
``CAPTURE_VIEWPORT``, always shown whole: the window moves, the page inside it
is never cropped. ``prep`` readies the page off camera. A shot with an
``act`` is filmed while the act drives the page with the gestures of
``recording`` (a visible cursor, typing, dragging); keep an act under seven
seconds, the length of a scene once the window is in. A shot without one is
a still, which may carry a ``callout``: ``(x, y, w, h)`` in CSS pixels, a
detail lifted out of the page and floated in front of the window.

A module on the home dashboard with no scene here still gets a generic one
(its registry description and a capture of its page), and the build prints a
warning: write it a proper entry.
"""

import time

from .ai_stub import REPLY
from .recording import click, drag, glide, point, type_text, wait

CAPTURE_VIEWPORT = {"width": 1440, "height": 900}

# Colours not taken from a module: the brand gradient of the app logo
# (daisyUI light primary to secondary) and the hue reserved for AI features.
BRAND_FROM = "#6d28d9"
BRAND_TO = "#db2777"

HUES = {
    "brand": "#8b5cf6",
    "indigo": "#6366f1",
    "sky": "#0ea5e9",
    "emerald": "#10b981",
    "teal": "#14b8a6",
    "amber": "#f59e0b",
    "orange": "#f97316",
    "purple": "#a855f7",
    "rose": "#f43f5e",
    "cyan": "#06b6d4",
    "slate": "#94a3b8",
    "lime": "#84cc16",
    "fuchsia": "#d946ef",
    "yellow": "#eab308",
    "pink": "#ec4899",
}


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


# -- scenes ------------------------------------------------------------------

SCENES = [
    {
        "slug": "dashboard",
        "color": "brand",
        "tagline": "Your whole day, at a glance.",
        "features": [
            ("sun", "Agenda, tasks and activity"),
            ("layout-grid", "Every module one click away"),
            ("search", "Unified search with Ctrl K"),
        ],
        "shots": [{"path": "/", "act": _search}],
    },
    {
        "slug": "files",
        "tagline": "Every file, wherever you work.",
        "features": [
            ("upload", "Drag and drop uploads"),
            ("eye", "Previews for PDF, video and code"),
            ("share-2", "Sharing, locking and comments"),
            ("server", "WebDAV for desktop and mobile"),
        ],
        "shots": [{"path": "/files", "prep": _mosaic, "act": _browse_photos}],
    },
    {
        "slug": "photos",
        "tagline": "Your memories, in the order they happened.",
        "features": [
            ("calendar-days", "A timeline by capture date"),
            ("book-image", "Albums for every trip"),
            ("scan-face", "Faces grouped and named"),
        ],
        "shots": [{"path": "/photos", "prep": _wait_for_images, "act": _photo_viewer}],
    },
    {
        "slug": "notes",
        "tagline": "Think in Markdown.",
        "features": [
            ("pen-line", "A rich Markdown editor"),
            ("book-open", "A daily journal"),
            ("tags", "Folders, tags and full-text search"),
        ],
        "shots": [{"path": "/notes", "prep": _open_note, "act": _write_note}],
    },
    {
        "slug": "chat",
        "tagline": "Conversations that happen now.",
        "features": [
            ("messages-square", "Direct and group chats"),
            ("smile-plus", "Reactions, pins and edits"),
            ("paperclip", "Attachments and search"),
        ],
        "shots": [{"path": "/chat/{conversation_uuid}", "act": _chat}],
    },
    {
        "slug": "ai",
        "name": "AI Assistants",
        "icon": "sparkles",
        "color": "pink",
        "tagline": "Assistants that know your work.",
        "features": [
            ("plug", "Any OpenAI-compatible model"),
            ("image", "Vision, tools and image generation"),
            ("brain", "Memory across conversations"),
        ],
        "shots": [{"path": "/chat/{bot_conversation_uuid}", "act": _ask_assistant}],
    },
    {
        "slug": "mail",
        "tagline": "Every inbox, one place.",
        "features": [
            ("key-round", "IMAP and SMTP, with OAuth2"),
            ("wand-sparkles", "AI summaries and replies"),
            ("folder-tree", "Folders, labels, drag and drop"),
        ],
        "shots": [{"path": "/mail", "act": _reply_to_mail}],
    },
    {
        "slug": "calendar",
        "tagline": "Time, well planned.",
        "features": [
            ("calendar-range", "Day, week, month and agenda"),
            ("repeat", "Recurring events and RSVP"),
            ("vote", "Scheduling polls and invitations"),
        ],
        "shots": [{"path": "/calendar", "settle_ms": 3000, "act": _calendar}],
    },
    {
        "slug": "projects",
        "tagline": "Ship work, together.",
        "features": [
            ("kanban", "Kanban boards and backlog"),
            ("chart-gantt", "Timeline and analytics"),
            ("user-check", "Assignees, priorities, due dates"),
        ],
        "shots": [{"path": "/projects/{project_uuid}/board", "act": _move_card}],
    },
    {
        "slug": "people",
        "tagline": "Everyone you work with.",
        "features": [
            ("book-user", "Personal and shared address books"),
            ("link", "Linked to workspace accounts"),
            ("contact", "vCard import and export"),
        ],
        "shots": [{"path": "/people", "act": _find_person}],
    },
    {
        "slug": "vault",
        "tagline": "Passwords, end-to-end encrypted.",
        "features": [
            ("lock", "Sealed in your browser"),
            ("fingerprint", "Signed by your account"),
            ("dices", "Password generator built in"),
        ],
        "shots": [
            {
                "path": "/vault",
                "prep": _unlock_vault,
                "settle_ms": 2500,
                "act": _reveal_password,
            }
        ],
    },
]

# The closing scene: what every module shares. The dashboard is captured in
# both themes for the light/dark card.
PLATFORM = {
    "title": "Built to be yours.",
    "tagline": "One app. Every tool. On your own server.",
    "cards": [
        ("search", "Unified search", "Files, mail, tasks and people in one box"),
        ("bell-ring", "Real-time", "Notifications, web push and live updates"),
        ("hard-drive-download", "Imports", "Bring your data over from Nextcloud"),
        ("smartphone", "Installable", "A PWA on desktop and mobile"),
        ("database", "SQLite or PostgreSQL", "Start small, migrate when you grow"),
        ("server-cog", "Any scale", "From a Raspberry Pi to Kubernetes"),
    ],
    "shots": {
        "light": {"path": "/", "theme": "light"},
        "dark": {"path": "/", "theme": "dark"},
    },
}

INTRO = {
    "title": "Workspace",
    "tagline": "The self-hosted productivity suite.",
    "subline": "Files, chat, mail, calendar, projects and more, in one app you control.",
}

OUTRO = {
    "title": "Workspace",
    "tagline": "Self-hosted. Open source. Yours.",
    "url": "github.com/JaaJSoft/workspace",
    "url_icon": "git-fork",
}
