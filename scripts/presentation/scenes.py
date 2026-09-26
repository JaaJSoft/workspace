"""What the presentation video says about each module, and what it shows.

One entry per scene, in playing order. A scene whose ``slug`` names a module
of ``core.module_registry`` takes its name, icon and colour from there, so a
rename or a new icon reaches the video on the next render. ``tagline`` and
``features`` are the words on screen; keep them short enough to read in the
four bars a scene lasts.

Each shot is a page of the seeded demo (``scripts/_screenshot_seed.py``),
captured at ``CAPTURE_VIEWPORT``. Coordinates (``focus``, ``callout``) are CSS
pixels of that viewport:

- ``focus``: ``(x, y, zoom)`` the camera slowly pushes into while the shot is
  on screen. Omit it for a gentle push toward the centre.
- ``callout``: ``(x, y, w, h)`` a detail lifted out of the page and floated
  in front of the window, enlarged. Omit it when nothing deserves one.

A module on the home dashboard with no scene here still gets a generic one
(its registry description and a capture of its page), and the build prints a
warning: write it a proper entry.
"""

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


# -- page preparation (runs on the loaded page before the capture) ----------


def _park_cursor(page):
    # Whatever sits under the viewport centre would be captured hovered.
    page.mouse.move(5, 5)


def _wait_for_images(page):
    # Thumbnails are lazy images fetched one request each.
    page.wait_for_function(
        "[...document.images].every(img => img.complete && img.naturalWidth > 0)",
        timeout=20000,
    )


def _files_view(mode):
    def prep(page):
        page.evaluate(f"document.querySelector('[title=\"{mode} view\"]').click()")
        _park_cursor(page)
        page.wait_for_timeout(600)
        _wait_for_images(page)

    return prep


def _photos_timeline(page):
    _park_cursor(page)
    page.wait_for_timeout(1500)
    _wait_for_images(page)


def _calendar_agenda(page):
    page.evaluate(
        """[...document.querySelectorAll('button')]
               .find(b => b.offsetParent && b.textContent.trim() === 'Agenda')
               ?.click()"""
    )
    _park_cursor(page)
    page.wait_for_timeout(1000)


def _open_text(text):
    def prep(page):
        page.click(f"text={text}")
        _park_cursor(page)
        page.wait_for_timeout(1500)

    return prep


def _person_panel(page):
    page.wait_for_function(
        "document.querySelector('#person-panel input[placeholder=\"Name\"]')?.value",
        timeout=15000,
    )
    _park_cursor(page)
    page.wait_for_timeout(500)


def _unlock_vault(page, context):
    page.fill("input[autocomplete='current-password']", context["vault_password"])
    page.fill("input[spellcheck='false']", context["vault_recovery_key"])
    page.click("button:has-text('Unlock')")
    page.get_by_test_id("vault-switcher").wait_for(timeout=60000)
    page.wait_for_timeout(2500)
    page.evaluate(
        """[...document.querySelectorAll('[data-entry-uuid], [role="row"]')]
               .find(el => el.offsetParent)?.click()"""
    )
    _park_cursor(page)
    page.wait_for_timeout(1500)


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
        "shots": [
            {"path": "/", "focus": (720, 330, 1.18), "callout": (324, 270, 792, 112)}
        ],
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
        "shots": [
            {
                "path": "/files/{photos_uuid}",
                "prep": _files_view("Mosaic"),
                "focus": (860, 420, 1.2),
            },
            {
                "path": "/files",
                "prep": _files_view("List"),
                "focus": (800, 300, 1.15),
            },
        ],
    },
    {
        "slug": "photos",
        "tagline": "Your memories, in the order they happened.",
        "features": [
            ("calendar-days", "A timeline by capture date"),
            ("book-image", "Albums for every trip"),
            ("scan-face", "Faces grouped and named"),
        ],
        "shots": [
            {"path": "/photos", "prep": _photos_timeline, "focus": (900, 380, 1.15)}
        ],
    },
    {
        "slug": "notes",
        "tagline": "Think in Markdown.",
        "features": [
            ("pen-line", "A rich Markdown editor"),
            ("book-open", "A daily journal"),
            ("tags", "Folders, tags and full-text search"),
        ],
        "shots": [
            {
                "path": "/notes",
                "prep": _open_text("Project kickoff"),
                "focus": (960, 380, 1.2),
            }
        ],
    },
    {
        "slug": "chat",
        "tagline": "Conversations that happen now.",
        "features": [
            ("messages-square", "Direct and group chats"),
            ("smile-plus", "Reactions, pins and edits"),
            ("paperclip", "Attachments and search"),
        ],
        "shots": [
            {
                "path": "/chat/{conversation_uuid}",
                "focus": (900, 480, 1.18),
                "callout": (1016, 324, 420, 66),
            }
        ],
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
        "shots": [{"path": "/chat/{bot_conversation_uuid}", "focus": (900, 450, 1.2)}],
    },
    {
        "slug": "mail",
        "tagline": "Every inbox, one place.",
        "features": [
            ("key-round", "IMAP and SMTP, with OAuth2"),
            ("wand-sparkles", "AI summaries and replies"),
            ("folder-tree", "Folders, labels, drag and drop"),
        ],
        "shots": [
            {
                "path": "/mail",
                "prep": _open_text("Palette variants for the hero"),
                "focus": (1000, 380, 1.18),
                "callout": (288, 180, 383, 80),
            }
        ],
    },
    {
        "slug": "calendar",
        "tagline": "Time, well planned.",
        "features": [
            ("calendar-range", "Day, week, month and agenda"),
            ("repeat", "Recurring events and RSVP"),
            ("vote", "Scheduling polls and invitations"),
        ],
        "shots": [
            {"path": "/calendar", "settle_ms": 3000, "focus": (820, 450, 1.15)},
            {
                "path": "/calendar",
                "settle_ms": 3000,
                "prep": _calendar_agenda,
                "focus": (820, 360, 1.15),
            },
        ],
    },
    {
        "slug": "projects",
        "tagline": "Ship work, together.",
        "features": [
            ("kanban", "Kanban boards and backlog"),
            ("chart-gantt", "Timeline and analytics"),
            ("user-check", "Assignees, priorities, due dates"),
        ],
        "shots": [
            {
                "path": "/projects/{project_uuid}/board",
                "focus": (760, 420, 1.15),
                "callout": (314, 207, 346, 122),
            },
            {
                "path": "/projects/{project_uuid}/timeline?scale=month",
                "settle_ms": 2500,
                "focus": (820, 420, 1.15),
            },
        ],
    },
    {
        "slug": "people",
        "tagline": "Everyone you work with.",
        "features": [
            ("book-user", "Personal and shared address books"),
            ("link", "Linked to workspace accounts"),
            ("contact", "vCard import and export"),
        ],
        "shots": [
            {
                "path": "/people?person={person_uuid}",
                "prep": _person_panel,
                "focus": (1000, 400, 1.15),
            }
        ],
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
                "focus": (800, 300, 1.15),
                "callout": (306, 104, 310, 44),
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
