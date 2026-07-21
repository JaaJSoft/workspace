"""Demo data and shot list for scripts/screenshots.py.

Everything here is deterministic (no faker, no randomness) so two runs
on the same UI produce visually identical captures. Dates are relative
to "now" so screenshots regenerated at release time always look
current.

Django models are imported inside functions: this module must stay
importable before ``django.setup()`` (``screenshots.py --list``).
"""

import io
from datetime import timedelta

AVATAR_SIZE = 256
AVATAR_COLORS = {
    "alex": (99, 102, 241),  # indigo
    "sam": (16, 185, 129),  # emerald
    "jordan": (245, 158, 11),  # amber
}


def _avatar_png(initials, color):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (AVATAR_SIZE, AVATAR_SIZE), color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 96
        )
    except OSError:
        font = ImageFont.load_default()
    left, top, right, bottom = draw.textbbox((0, 0), initials, font=font)
    draw.text(
        (
            (AVATAR_SIZE - (right - left)) / 2 - left,
            (AVATAR_SIZE - (bottom - top)) / 2 - top,
        ),
        initials,
        fill="white",
        font=font,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _photo_png(color):
    from PIL import Image

    img = Image.new("RGB", (800, 600), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n%%EOF\n"
)


def _backdate(instance, ts):
    """Rewrite auto_now(_add) timestamps after creation."""
    fields = {}
    for name in ("created_at", "updated_at"):
        if hasattr(instance, name):
            fields[name] = ts
    type(instance).objects.filter(pk=instance.pk).update(**fields)


def seed(username, password):
    """Create the demo users and per-module data. Returns URL context."""
    from django.contrib.auth.models import Group, User
    from django.utils import timezone

    from workspace.core import setting_keys
    from workspace.core.changelog import get_latest_version
    from workspace.users.models import UserPresence
    from workspace.users.services.avatar import process_and_save_avatar
    from workspace.users.services.settings import set_setting

    now = timezone.localtime()

    users = {}
    for uname, first, last, initials in [
        (username, "Alex", "Martin", "AM"),
        ("sam", "Sam", "Rivera", "SR"),
        ("jordan", "Jordan", "Lee", "JL"),
    ]:
        user = User.objects.create_user(
            username=uname,
            email=f"{uname}@workspace.dev",
            first_name=first,
            last_name=last,
            password=password,
        )
        png = _avatar_png(initials, AVATAR_COLORS.get(uname, (99, 102, 241)))
        process_and_save_avatar(user, io.BytesIO(png), 0, 0, AVATAR_SIZE, AVATAR_SIZE)
        UserPresence.objects.get_or_create(user=user, defaults={"last_seen": now})
        # Neither the onboarding tour nor the "What's new" modal should
        # pop over the captures.
        set_setting(user, setting_keys.MODULE, setting_keys.ONBOARDING_COMPLETED, True)
        latest = get_latest_version()
        if latest:
            set_setting(
                user,
                setting_keys.MODULE,
                setting_keys.CHANGELOG_LAST_SEEN_VERSION,
                latest,
            )
        users[uname] = user

    alex, sam, jordan = users[username], users["sam"], users["jordan"]
    design_team = Group.objects.create(name="Design Team")
    for user in (alex, sam, jordan):
        user.groups.add(design_team)

    context = {}
    _seed_files(alex, sam, design_team, now)
    _seed_notes(alex, now)
    context["conversation_uuid"] = _seed_chat(alex, sam, jordan, now)
    _seed_calendar(alex, sam, jordan, now)
    _seed_mail(alex, now)
    context["project_uuid"] = _seed_projects(alex, sam, jordan, now)
    return context


def _seed_files(alex, sam, group, now):
    from django.core.files.base import ContentFile

    from workspace.files.models import FileEvent, FileFavorite, PinnedFolder
    from workspace.files.services import FileService

    def backdate_file(f, ts):
        _backdate(f, ts)
        FileEvent.objects.filter(file=f).update(created_at=ts)

    documents = FileService.create_folder(alex, "Documents")
    photos = FileService.create_folder(
        alex, "Photos", icon="image", color="text-warning"
    )
    FileService.create_folder(alex, "Archive", icon="archive", color="text-neutral")

    csv = "month,revenue,expenses\nJanuary,12400,8100\nFebruary,13950,8420\nMarch,15200,9010\n"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<circle cx="32" cy="32" r="28" fill="#6366f1"/></svg>'
    )
    files = [
        (documents, "Quarterly report.pdf", _MINIMAL_PDF, "application/pdf", 26),
        (documents, "budget-2026.csv", csv.encode(), "text/csv", 20),
        (
            None,
            "Roadmap.md",
            b"# Roadmap\n\n- Q3: mobile apps\n- Q4: offline mode\n",
            "text/markdown",
            8,
        ),
        (None, "logo.svg", svg.encode(), "image/svg+xml", 5),
        (photos, "team-offsite.png", _photo_png((59, 130, 246)), "image/png", 3),
        (photos, "product-shot.png", _photo_png((16, 185, 129)), "image/png", 2),
    ]
    report = None
    for parent, name, data, mime, days_ago in files:
        f = FileService.create_file(
            alex, name, parent, content=ContentFile(data, name=name), mime_type=mime
        )
        backdate_file(f, now - timedelta(days=days_ago, hours=3))
        if name == "Quarterly report.pdf":
            report = f

    FileFavorite.objects.create(owner=alex, file=report)
    FileFavorite.objects.create(owner=alex, file=photos)
    PinnedFolder.objects.create(owner=alex, folder=photos, position=0)

    # Activity from another user, on a group drive the demo user can see:
    # the dashboard feed excludes the viewer's own actions.
    shared = FileService.create_folder(sam, "Brand assets", group=group, icon="palette")
    for name, data, mime, hours_ago in [
        (
            "styleguide.md",
            b"# Styleguide\n\n## Colors\n- Indigo `#6366f1`\n",
            "text/markdown",
            26,
        ),
        ("banner.png", _photo_png((245, 158, 11)), "image/png", 4),
    ]:
        f = FileService.create_file(
            sam,
            name,
            shared,
            content=ContentFile(data, name=name),
            mime_type=mime,
            group=group,
        )
        backdate_file(f, now - timedelta(hours=hours_ago))


def _seed_notes(alex, now):
    from django.core.files.base import ContentFile

    from workspace.files.models import File
    from workspace.files.services import FileService
    from workspace.notes.ui.views import _ensure_default_folders

    prefs, _ = _ensure_default_folders(alex)
    notes_folder = File.objects.get(uuid=prefs["defaultFolderUuid"])
    journal_folder = File.objects.get(uuid=prefs["journalFolderUuid"])
    notes = [
        (
            notes_folder,
            "Project kickoff.md",
            "# Project kickoff\n\n## Goals\n\n- Ship the marketing site refresh\n"
            "- Migrate the blog\n- Improve Core Web Vitals\n\n"
            "## Open questions\n\n- Do we keep the old pricing page?\n",
            30,
        ),
        (
            notes_folder,
            "Reading list.md",
            "# Reading list\n\n- [ ] Shape Up\n- [x] The Design of Everyday Things\n"
            "- [ ] Refactoring UI\n",
            52,
        ),
        (
            journal_folder,
            f"{now:%Y-%m-%d}.md",
            f"# {now:%A, %B %-d}\n\nSketched the new hero section this morning, "
            "then paired with Sam on the palette. Feeling good about the direction.\n",
            2,
        ),
    ]
    for parent, name, body, hours_ago in notes:
        f = FileService.create_file(
            alex,
            name,
            parent,
            content=ContentFile(body.encode(), name=name),
            mime_type="text/markdown",
        )
        _backdate(f, now - timedelta(hours=hours_ago))


def _seed_chat(alex, sam, jordan, now):
    from workspace.chat.models import Conversation, ConversationMember, Message
    from workspace.chat.services.conversations import get_or_create_dm
    from workspace.chat.services.rendering import render_message_body

    def post(conversation, author, body, ts):
        msg = Message.objects.create(
            conversation=conversation,
            author=author,
            body=body,
            body_html=render_message_body(body),
        )
        _backdate(msg, ts)
        return ts

    dm = get_or_create_dm(alex, sam)
    post(dm, sam, "Did you see the new board? 👀", now - timedelta(hours=5))
    last = post(
        dm,
        alex,
        "Yes! Moving the launch tasks over now.",
        now - timedelta(hours=5, minutes=-4),
    )
    Conversation.objects.filter(pk=dm.pk).update(updated_at=last)

    group = Conversation.objects.create(
        kind=Conversation.Kind.GROUP,
        title="Design Team",
        created_by=alex,
    )
    ConversationMember.objects.bulk_create(
        ConversationMember(conversation=group, user=u, last_read_at=now)
        for u in (alex, sam, jordan)
    )
    thread = [
        (sam, "Morning! The new palette is live on the staging site 🎨", 130),
        (jordan, "Looks great. The contrast on the hero text is much better.", 121),
        (alex, "Agreed. Can we try the **indigo** variant for the buttons?", 104),
        (sam, "Sure, pushing it now — refresh in a minute.", 98),
        (jordan, "🚀", 95),
    ]
    for author, body, minutes_ago in thread:
        last = post(group, author, body, now - timedelta(minutes=minutes_ago))
    Conversation.objects.filter(pk=group.pk).update(updated_at=last)
    return str(group.uuid)


def _seed_calendar(alex, sam, jordan, now):
    from workspace.calendar.models import Calendar, Event, EventMember

    personal = Calendar.objects.create(name="Personal", color="primary", owner=alex)
    team = Calendar.objects.create(name="Team", color="secondary", owner=alex)

    today = now.replace(minute=0, second=0, microsecond=0)
    monday = today.replace(hour=0) - timedelta(days=today.weekday())

    def event(cal, title, start, hours=1, **kwargs):
        return Event.objects.create(
            calendar=cal,
            owner=alex,
            title=title,
            start=start,
            end=start + timedelta(hours=hours),
            **kwargs,
        )

    # Keep it later than "now" so the dashboard's upcoming widget shows
    # it, but inside working hours even when the script runs late.
    sprint_hour = min(max(now.hour + 1, 9), 20)
    sprint = event(
        team,
        "Sprint planning",
        today.replace(hour=sprint_hour),
        location="Meeting room 2",
    )
    event(personal, "Lunch with Sam", today.replace(hour=12, minute=30), 1)
    review = event(
        team,
        "Design review",
        monday + timedelta(days=2, hours=14),
        1,
        description="Walk through the new landing page",
    )
    event(team, "1:1 with Jordan", monday + timedelta(days=3, hours=11), 1)
    event(
        personal,
        "Yoga",
        monday.replace(hour=18),
        1,
        recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
    )
    release = monday + timedelta(days=4)
    Event.objects.create(
        calendar=team,
        owner=alex,
        title="Release day 🚀",
        start=release.replace(hour=0),
        end=release.replace(hour=0) + timedelta(days=1),
        all_day=True,
    )
    for ev, user, status in [
        (sprint, sam, EventMember.Status.ACCEPTED),
        (sprint, jordan, EventMember.Status.ACCEPTED),
        (review, sam, EventMember.Status.ACCEPTED),
        (review, jordan, EventMember.Status.PENDING),
    ]:
        EventMember.objects.create(event=ev, user=user, status=status)


def _seed_mail(alex, now):
    from workspace.mail.models import MailAccount, MailFolder, MailMessage

    account = MailAccount.objects.create(
        owner=alex,
        email="alex@workspace.dev",
        username="alex@workspace.dev",
        imap_host="imap.workspace.dev",
        smtp_host="smtp.workspace.dev",
    )
    account.set_password("demo")
    account.save()

    folders = {}
    for name, display, ftype in [
        ("INBOX", "Inbox", MailFolder.FolderType.INBOX),
        ("Sent", "Sent", MailFolder.FolderType.SENT),
        ("Drafts", "Drafts", MailFolder.FolderType.DRAFTS),
        ("Archive", "Archive", MailFolder.FolderType.ARCHIVE),
        ("Trash", "Trash", MailFolder.FolderType.TRASH),
    ]:
        folders[name] = MailFolder.objects.create(
            account=account,
            name=name,
            display_name=display,
            folder_type=ftype,
        )

    inbox = folders["INBOX"]
    messages = [
        (
            "Sam Rivera",
            "sam@workspace.dev",
            "Palette variants for the hero",
            "I attached the two indigo variants we discussed — I prefer the deeper one.",
            2,
            False,
            True,
        ),
        (
            "Jordan Lee",
            "jordan@workspace.dev",
            "Re: Launch checklist",
            "DNS is done, staging is green. Remaining items are copy review and the 404 page.",
            5,
            False,
            False,
        ),
        (
            "GitHub",
            "notifications@github.com",
            "[workspace] Release v0.31.0 published",
            "Release v0.31.0 has been published. View the changelog for details.",
            9,
            True,
            False,
        ),
        (
            "Product Weekly",
            "digest@productweekly.dev",
            "Issue #142 — Onboarding that works",
            "This week: onboarding flows, pricing page teardowns, and a great case study.",
            26,
            True,
            False,
        ),
        (
            "Cloud Status",
            "status@cloudprovider.dev",
            "Maintenance window on Saturday",
            "Scheduled maintenance this Saturday 02:00-04:00 UTC. No downtime expected.",
            31,
            True,
            False,
        ),
        (
            "Sam Rivera",
            "sam@workspace.dev",
            "Offsite photos",
            "Uploaded the offsite photos to the shared drive — some really good ones!",
            50,
            True,
            False,
        ),
    ]
    for uid, (
        from_name,
        from_email,
        subject,
        body,
        hours_ago,
        is_read,
        starred,
    ) in enumerate(messages, start=1):
        MailMessage.objects.create(
            account=account,
            folder=inbox,
            imap_uid=uid,
            subject=subject,
            from_name=from_name,
            from_email=from_email,
            to_addresses=["alex@workspace.dev"],
            date=now - timedelta(hours=hours_ago),
            snippet=body,
            body_text=body,
            body_html=f"<p>{body}</p>",
            is_read=is_read,
            is_starred=starred,
        )
    inbox.message_count = len(messages)
    inbox.unread_count = sum(1 for m in messages if not m[5])
    inbox.save(update_fields=["message_count", "unread_count"])


def _seed_projects(alex, sam, jordan, now):
    from workspace.projects.models import Label, ProjectMember, Task
    from workspace.projects.services.projects import create_project

    project = create_project(
        alex,
        name="Website Redesign",
        description="Q3 marketing site overhaul",
    )
    ProjectMember.objects.create(project=project, user=sam)
    ProjectMember.objects.create(project=project, user=jordan)

    statuses = {s.name: s for s in project.statuses.all()}
    labels = {
        name: Label.objects.create(project=project, name=name, color=color)
        for name, color in [
            ("Design", "primary"),
            ("Backend", "secondary"),
            ("Bug", "error"),
            ("Content", "accent"),
        ]
    }
    tasks = [
        ("Design new landing page hero", "In progress", "high", 3, [sam], ["Design"]),
        ("Migrate blog articles", "In progress", "medium", None, [jordan], ["Content"]),
        ("Fix mobile navigation overlap", "To do", "urgent", 1, [alex], ["Bug"]),
        ("Set up newsletter signup API", "To do", "medium", None, [sam], ["Backend"]),
        ("Write pricing page copy", "To do", "low", None, [], ["Content"]),
        ("Audit current site performance", "Done", "medium", None, [alex], []),
        ("Pick a new color palette", "Done", "low", None, [sam], ["Design"]),
        ("Dark mode support", "Backlog", "low", None, [], ["Design"]),
        ("Customer testimonials section", "Backlog", "medium", None, [], ["Content"]),
    ]
    for position, (
        title,
        status,
        priority,
        due_days,
        assignees,
        task_labels,
    ) in enumerate(tasks):
        task = Task.objects.create(
            project=project,
            title=title,
            status=statuses[status],
            priority=priority,
            position=position,
            created_by=alex,
            due_date=(now + timedelta(days=due_days)).date() if due_days else None,
        )
        task.assignees.set(assignees)
        task.labels.set([labels[name] for name in task_labels])
    return str(project.uuid)


# ---------------------------------------------------------------------------
# Shot list — name matches the file in docs/images/, path is formatted with
# the context returned by seed(). "prep" runs on the loaded page before the
# capture (switch views, open panels, ...).
# ---------------------------------------------------------------------------


def _files_view(mode):
    # Dispatch the click from JS: the drawer layout overlaps the toggle
    # enough that Playwright's hit-testing refuses a trusted click. The
    # choice persists per user, so each files shot sets its own view.
    def prep(page):
        page.evaluate(f"document.querySelector('[title=\"{mode} view\"]').click()")
        page.wait_for_timeout(600)

    return prep


def _calendar_agenda(page):
    # Several responsive variants of the switcher exist; click the
    # visible one (offsetParent is null for hidden elements).
    page.evaluate(
        """[...document.querySelectorAll('button')]
               .find(b => b.offsetParent && b.textContent.trim() === 'Agenda')
               ?.click()"""
    )
    # Park the cursor so no event hover-popover sneaks into the capture.
    page.mouse.move(5, 5)
    page.wait_for_timeout(1000)


def _open_first_mail(page):
    page.click("text=Palette variants for the hero")
    page.wait_for_timeout(1500)


def _open_first_note(page):
    page.click("text=Project kickoff")
    page.wait_for_timeout(1500)


SHOTS = [
    {"name": "home", "path": "/"},
    {"name": "files_1", "path": "/files", "prep": _files_view("Mosaic")},
    {"name": "files_2", "path": "/files", "prep": _files_view("List")},
    {"name": "chat_1", "path": "/chat/{conversation_uuid}"},
    {"name": "calendar_1", "path": "/calendar", "settle_ms": 3000},
    {
        "name": "calendar_2",
        "path": "/calendar",
        "settle_ms": 3000,
        "prep": _calendar_agenda,
    },
    {"name": "mail_1", "path": "/mail", "prep": _open_first_mail},
    {"name": "notes_1", "path": "/notes", "prep": _open_first_note},
    {"name": "projects_1", "path": "/projects/{project_uuid}/board"},
]
