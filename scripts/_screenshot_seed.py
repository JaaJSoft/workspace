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
        # Pillow's bundled scalable font: initials stay readable on a host
        # without DejaVu instead of shrinking to the tiny bitmap default.
        font = ImageFont.load_default(size=96)
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


def _photo_png(start, end):
    """A diagonal two-tone gradient, standing in for a photograph.

    Flat fills read as placeholders in a thumbnail grid; a gradient gives
    the mosaic capture the texture a real photo library would have.
    """
    from PIL import Image

    vertical = Image.linear_gradient("L")
    diagonal = Image.blend(vertical, vertical.transpose(Image.Transpose.ROTATE_90), 0.5)
    img = Image.composite(
        Image.new("RGB", diagonal.size, end),
        Image.new("RGB", diagonal.size, start),
        diagonal,
    ).resize((800, 600), Image.Resampling.BICUBIC)
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
    context["photos_uuid"] = _seed_files(alex, sam, design_team, now)
    _seed_notes(alex, now)
    context["conversation_uuid"] = _seed_chat(alex, sam, jordan, now)
    context["bot_conversation_uuid"] = _seed_ai(alex, now)
    _seed_calendar(alex, sam, jordan, now)
    _seed_mail(alex, now)
    context["project_uuid"], context["task_uuid"] = _seed_projects(
        alex, sam, jordan, now
    )
    context["person_uuid"] = _seed_people(alex, sam, jordan, design_team)
    _seed_notifications(alex, sam, jordan, now)
    return context


def _seed_files(alex, sam, group, now):
    from django.core.files.base import ContentFile

    from workspace.files.models import FileEvent, FileFavorite, FileTag, PinnedFolder
    from workspace.files.services import FileService
    from workspace.users.services.settings import set_setting

    # Tile size 2 (140px) instead of the 3 (180px) default: the mosaic
    # capture is 990px wide, so the smaller tiles fill it with two dense
    # rows instead of two sparse ones.
    set_setting(alex, "files", "preferences", {"mosaicTileSize": 2})

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
    # The mosaic shot is taken inside Photos: a thumbnail grid is what the
    # view exists for, so it needs enough images to actually fill a grid.
    pictures = [
        ("product-shot.png", (16, 185, 129), (5, 150, 105), 2),
        ("team-offsite.png", (59, 130, 246), (14, 165, 233), 3),
        ("keynote-stage.png", (99, 102, 241), (139, 92, 246), 4),
        ("office-tour.png", (245, 158, 11), (249, 115, 22), 6),
        ("conference-booth.png", (236, 72, 153), (219, 39, 119), 7),
        ("workshop.png", (20, 184, 166), (6, 182, 212), 9),
        ("launch-party.png", (168, 85, 247), (217, 70, 239), 11),
        ("hero-banner.png", (239, 68, 68), (249, 115, 22), 13),
        ("city-skyline.png", (30, 64, 175), (67, 56, 202), 15),
        ("desk-setup.png", (100, 116, 139), (71, 85, 105), 17),
        ("whiteboard.png", (34, 197, 94), (132, 204, 22), 19),
        ("meetup-crowd.png", (2, 132, 199), (56, 189, 248), 22),
    ]
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
    ] + [
        (photos, name, _photo_png(start, end), "image/png", days_ago)
        for name, start, end, days_ago in pictures
    ]
    created = {}
    for parent, name, data, mime, days_ago in files:
        f = FileService.create_file(
            alex, name, parent, content=ContentFile(data, name=name), mime_type=mime
        )
        backdate_file(f, now - timedelta(days=days_ago, hours=3))
        created[name] = f
    report = created["Quarterly report.pdf"]

    FileFavorite.objects.create(owner=alex, file=report)
    FileFavorite.objects.create(owner=alex, file=photos)
    PinnedFolder.objects.create(owner=alex, folder=photos, position=0)

    # Tags fill the list view's Tags column and the sidebar's pinned
    # tags; the same tags show up on notes, which are files too.
    tags = _seed_tags(
        alex,
        [
            ("client", "#3b82f6", True),
            ("2026", "#22c55e", False),
            ("draft", "#f97316", True),
        ],
    )
    for name, tag_names in [
        ("Quarterly report.pdf", ["client", "2026"]),
        ("budget-2026.csv", ["2026"]),
        ("Roadmap.md", ["draft"]),
        ("logo.svg", ["client"]),
        ("hero-banner.png", ["draft"]),
        ("product-shot.png", ["client"]),
    ]:
        for tag_name in tag_names:
            FileTag.objects.create(file=created[name], tag=tags[tag_name])

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
        ("banner.png", _photo_png((245, 158, 11), (249, 115, 22)), "image/png", 4),
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

    return str(photos.uuid)


def _seed_tags(owner, specs):
    """Create (name, color, pinned) tags for *owner*; returns them by name."""
    from workspace.files.models import Tag

    return {
        name: Tag.objects.create(
            owner=owner, name=name, color=color, is_favorite=pinned
        )
        for name, color, pinned in specs
    }


def _seed_notes(alex, now):
    from django.core.files.base import ContentFile

    from workspace.files.models import File, FileTag
    from workspace.files.services import FileService
    from workspace.notes.ui.views import _ensure_default_folders

    meeting = _seed_tags(alex, [("meeting", "#a855f7", True)])["meeting"]

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
            f"# {now:%A, %B} {now.day}\n\nSketched the new hero section this morning, "
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
        if name == "Project kickoff.md":
            FileTag.objects.create(file=f, tag=meeting)


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


def _seed_ai(alex, now):
    from django.contrib.auth.models import User

    from workspace.ai.models import BotProfile
    from workspace.chat.models import Conversation, Message
    from workspace.chat.services.conversations import get_or_create_dm
    from workspace.chat.services.rendering import render_message_body
    from workspace.users.services.avatar import process_and_save_avatar

    nova = User.objects.create_user(
        username="nova",
        email="nova@workspace.dev",
        first_name="Nova",
    )
    png = _avatar_png("N", (139, 92, 246))  # violet
    process_and_save_avatar(nova, io.BytesIO(png), 0, 0, AVATAR_SIZE, AVATAR_SIZE)
    BotProfile.objects.create(
        user=nova,
        is_public=True,
        description="General-purpose assistant for the whole workspace.",
        system_prompt="You are Nova, a helpful workspace assistant.",
    )

    dm = get_or_create_dm(alex, nova)
    thread = [
        (alex, "Where does the website redesign stand? Give me a quick recap.", 47),
        (
            nova,
            "Here's where **Website Redesign** stands right now:\n\n"
            "- **In progress** — the new landing page hero (Sam) and the blog "
            "migration (Jordan)\n"
            "- **Up next** — the mobile navigation fix is *urgent* and due "
            "tomorrow\n"
            "- **Done** — the performance audit and the new color palette\n\n"
            "The board is on track for the release on Friday. 🚀",
            46,
        ),
        (alex, "Great. Draft a short announcement for the launch, please.", 43),
        (
            nova,
            "Here's a first draft:\n\n"
            "> Our website just got a fresh coat of paint 🎨 — a faster, "
            "cleaner site with a brand-new look, redesigned from the ground "
            "up. Take a look and tell us what you think!\n\n"
            "Want a longer version for the blog as well?",
            42,
        ),
    ]
    last = None
    for author, body, minutes_ago in thread:
        msg = Message.objects.create(
            conversation=dm,
            author=author,
            body=body,
            body_html=render_message_body(body),
        )
        last = now - timedelta(minutes=minutes_ago)
        _backdate(msg, last)
    Conversation.objects.filter(pk=dm.pk).update(updated_at=last)
    return str(dm.uuid)


def _seed_calendar(alex, sam, jordan, now):
    from workspace.calendar.models import Calendar, Event, EventMember
    from workspace.calendar.services.recurrence_rule import apply_rule

    personal = Calendar.objects.create(name="Personal", color="primary", owner=alex)
    team = Calendar.objects.create(name="Team", color="secondary", owner=alex)

    today = now.replace(minute=0, second=0, microsecond=0)
    monday = today.replace(hour=0) - timedelta(days=today.weekday())
    # From Friday on, most of the week's meetings would already be behind
    # us and the agenda view would show nothing but the recurring yoga;
    # place them in the coming week instead.
    if today.weekday() >= 4:
        monday += timedelta(days=7)

    def event(cal, title, start, hours=1, rule="", **kwargs):
        ev = Event(
            calendar=cal,
            owner=alex,
            title=title,
            start=start,
            end=start + timedelta(hours=hours),
            **kwargs,
        )
        # apply_rule owns is_recurring/recurrence_until; setting them here
        # (or passing the rule to create()) would leave the row unexpandable.
        apply_rule(ev, rule)
        ev.save()
        return ev

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
        rule="RRULE:FREQ=WEEKLY",
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
            to_addresses=[{"name": alex.get_full_name(), "email": account.email}],
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


def _offset_date(now, days):
    return (now + timedelta(days=days)).date() if days is not None else None


def _seed_projects(alex, sam, jordan, now):
    from workspace.projects.models import (
        Epic,
        Label,
        Project,
        ProjectMember,
        Task,
        TaskComment,
        TaskEvent,
    )
    from workspace.projects.services.events import record_task_event
    from workspace.projects.services.projects import create_project
    from workspace.projects.services.tasks import create_task

    project = create_project(
        alex,
        name="Website Redesign",
        description="Q3 marketing site overhaul",
    )
    project.estimate_unit = Project.EstimateUnit.POINTS
    project.save(update_fields=["estimate_unit"])
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
    hero_description = (
        "Full-bleed hero with the new palette and a single, focused CTA.\n\n"
        "## Acceptance criteria\n\n"
        "- Headline and CTA follow the new styleguide\n"
        "- LCP stays under 2s on mobile\n"
        "- Light and dark theme variants\n"
    )
    epics = {
        name: Epic.objects.create(
            project=project,
            name=name,
            color=color,
            target_date=(now + timedelta(days=target_days)).date(),
        )
        for name, color, target_days in [
            ("Launch", "#6366f1", 12),
            ("Content refresh", "#06b6d4", 26),
        ]
    }

    # (title, status, priority, start, due, estimate, assignees, labels, epic,
    # created, completed) - start/due/created/completed are day offsets from
    # now. Start+due draws a bar on the timeline, a due date alone a marker;
    # created/completed spread the flow chart over the past weeks.
    def spec(
        title,
        status,
        priority,
        *,
        start=None,
        due=None,
        estimate=None,
        assignees=(),
        labels=(),
        epic=None,
        created,
        completed=None,
    ):
        """One task; start/due/created/completed are day offsets from now.

        Start+due draws a bar on the timeline, a due date alone a marker;
        created/completed spread the analytics flow chart over past weeks.
        """
        return locals()

    tasks = [
        spec(
            "Design new landing page hero",
            "In progress",
            "high",
            start=-4,
            due=3,
            estimate=5,
            assignees=[sam],
            labels=["Design"],
            epic="Launch",
            created=-18,
        ),
        spec(
            "Migrate blog articles",
            "In progress",
            "medium",
            start=-6,
            due=9,
            estimate=3,
            assignees=[jordan],
            labels=["Content"],
            epic="Content refresh",
            created=-12,
        ),
        spec(
            "Fix mobile navigation overlap",
            "To do",
            "urgent",
            due=1,
            estimate=2,
            assignees=[alex],
            labels=["Bug"],
            epic="Launch",
            created=-2,
        ),
        spec(
            "Set up newsletter signup API",
            "To do",
            "medium",
            start=2,
            due=8,
            estimate=3,
            assignees=[sam],
            labels=["Backend"],
            epic="Launch",
            created=-9,
        ),
        spec(
            "Write pricing page copy",
            "To do",
            "low",
            start=6,
            due=13,
            estimate=1,
            labels=["Content"],
            epic="Content refresh",
            created=-5,
        ),
        spec(
            "Audit current site performance",
            "Done",
            "medium",
            start=-20,
            due=-13,
            estimate=8,
            assignees=[alex],
            epic="Launch",
            created=-27,
            completed=-13,
        ),
        spec(
            "Pick a new color palette",
            "Done",
            "low",
            start=-15,
            due=-8,
            estimate=2,
            assignees=[sam],
            labels=["Design"],
            epic="Launch",
            created=-20,
            completed=-8,
        ),
        spec(
            "Dark mode support",
            "Backlog",
            "low",
            estimate=5,
            labels=["Design"],
            created=-33,
        ),
        spec(
            "Customer testimonials section",
            "Backlog",
            "medium",
            labels=["Content"],
            epic="Content refresh",
            created=-40,
        ),
    ]
    hero = None
    for t in tasks:
        title, assignees = t["title"], t["assignees"]
        task = create_task(
            project,
            alex,
            title=title,
            description=hero_description if title.startswith("Design new") else "",
            status=statuses[t["status"]],
            priority=t["priority"],
            start_date=_offset_date(now, t["start"]),
            due_date=_offset_date(now, t["due"]),
            estimate=t["estimate"],
            assignees=assignees,
            labels=[labels[name] for name in t["labels"]],
            epic=epics[t["epic"]] if t["epic"] else None,
        )
        if hero is None:
            hero = task
        created_at = now + timedelta(days=t["created"], hours=-2)
        _backdate(task, created_at)
        task.events.filter(type=TaskEvent.Type.CREATED).update(created_at=created_at)
        if t["completed"] is not None:
            # The move onto the board is what cycle time is measured from.
            started_at = now + timedelta(days=t["start"], hours=-1)
            moved_event = record_task_event(
                task,
                type=TaskEvent.Type.MOVED,
                actor=assignees[0] if assignees else alex,
                from_status=statuses["To do"],
                to_status=statuses["In progress"],
            )
            _backdate(moved_event, started_at)
            completed_at = now + timedelta(days=t["completed"], hours=-1)
            Task.objects.filter(pk=task.pk).update(completed_at=completed_at)
            done_event = record_task_event(
                task,
                type=TaskEvent.Type.COMPLETED,
                actor=assignees[0] if assignees else alex,
                from_status=statuses["In progress"],
                to_status=statuses["Done"],
            )
            _backdate(done_event, completed_at)

    # Comment thread for the task-panel capture.
    for author, body, hours_ago in [
        (sam, "First iteration is on staging — used the deeper indigo.", 6),
        (jordan, "Looks sharp. Can we A/B the headline before launch?", 3),
    ]:
        comment = TaskComment.objects.create(task=hero, author=author, body=body)
        _backdate(comment, now - timedelta(hours=hours_ago))

    return str(project.uuid), str(hero.uuid)


def _seed_people(alex, sam, jordan, group):
    from datetime import date

    from workspace.people.services.avatar import save_avatar
    from workspace.people.services.lists import add_members, create_list
    from workspace.people.services.persons import create_person

    def contact(scope, given, family, organization="", title="", **fields):
        return create_person(
            **scope,
            display_name=f"{given} {family}",
            given_name=given,
            family_name=family,
            organization=organization,
            title=title,
            **fields,
        )

    mine = {"owner": alex}
    # The capture opens this one: enough on the card to fill every section
    # of the panel (several emails and phones, an address, a list, notes).
    camille = contact(
        mine,
        "Camille",
        "Roux",
        "Atelier Nord",
        "Art director",
        birthday=date(1988, 4, 12),
        emails=[
            {"value": "camille@ateliernord.fr", "type": "work"},
            {"value": "camille.roux@gmail.com", "type": "home"},
        ],
        phones=[
            {"value": "+33 6 12 34 56 78", "type": "cell"},
            {"value": "+33 4 72 00 12 34", "type": "work"},
        ],
        addresses=[
            {
                "street": "12 rue des Lilas",
                "city": "Lyon",
                "region": "",
                "postal_code": "69003",
                "country": "France",
                "type": "work",
            }
        ],
        notes="Met at the Lyon design meetup. Prefers a call over email for "
        "anything urgent.",
    )
    png = _avatar_png("CR", (244, 63, 94))  # rose
    save_avatar(camille, io.BytesIO(png), 0, 0, AVATAR_SIZE, AVATAR_SIZE)

    priya = contact(
        mine,
        "Priya",
        "Nair",
        "Bright Labs",
        "Product manager",
        emails=[{"value": "priya@brightlabs.io", "type": "work"}],
        phones=[{"value": "+44 20 7946 0123", "type": "work"}],
    )
    elena = contact(
        mine,
        "Elena",
        "Fischer",
        "Fischer & Co",
        "Accountant",
        emails=[{"value": "elena@fischer-co.de", "type": "work"}],
    )
    contact(
        mine,
        "Marco",
        "Bianchi",
        title="Photographer",
        emails=[{"value": "hello@marcobianchi.photo", "type": "work"}],
        phones=[{"value": "+39 333 123 4567", "type": "cell"}],
    )
    contact(
        mine,
        "Tom",
        "Okafor",
        emails=[{"value": "tom.okafor@gmail.com", "type": "home"}],
        phones=[{"value": "+1 415 555 0134", "type": "cell"}],
    )
    contact(
        mine,
        "Nina",
        "Berg",
        emails=[{"value": "nina.berg@icloud.com", "type": "home"}],
    )
    # Linked contacts show the account's own avatar and name.
    for user in (sam, jordan):
        contact(
            mine,
            user.first_name,
            user.last_name,
            emails=[{"value": user.email, "type": "work"}],
            linked_user=user,
        )
    clients = create_list(owner=alex, name="Clients")
    add_members(clients, [camille, priya, elena])

    # A group only earns its sidebar row once it holds a contact.
    shared = {"group": group}
    lucas = contact(
        shared,
        "Lucas",
        "Moreau",
        "Printworks",
        "Account manager",
        emails=[{"value": "lucas@printworks.fr", "type": "work"}],
        phones=[{"value": "+33 1 44 55 66 77", "type": "work"}],
    )
    aiko = contact(
        shared,
        "Aiko",
        "Tanaka",
        "Glyph Foundry",
        "Type designer",
        emails=[{"value": "aiko@glyphfoundry.jp", "type": "work"}],
    )
    vendors = create_list(group=group, name="Vendors")
    add_members(vendors, [lucas, aiko])
    return str(camille.uuid)


def _seed_notifications(alex, sam, jordan, now):
    # Rows are created directly instead of going through notify(): the
    # service also queues a Celery push task and publishes SSE, neither of
    # which exists in the throwaway environment. Icons still come from the
    # module registry so they match what notify() would produce.
    from workspace.core.module_registry import registry
    from workspace.notifications.models import Notification

    entries = [
        (
            "chat",
            sam,
            "Sam Rivera mentioned you in Design Team",
            "Agreed. Can we try the indigo variant for the buttons?",
            "/chat",
            "normal",
            None,
            timedelta(minutes=25),
        ),
        (
            "calendar",
            jordan,
            "Invitation: Design review",
            "Wednesday 14:00 · Walk through the new landing page",
            "/calendar",
            "normal",
            None,
            timedelta(hours=2),
        ),
        (
            "projects",
            sam,
            "Sam Rivera assigned you WR-3",
            "Fix mobile navigation overlap — due tomorrow",
            "/projects",
            "high",
            None,
            timedelta(hours=4),
        ),
        (
            "files",
            sam,
            "Sam Rivera shared Brand assets with you",
            "2 files · styleguide.md, banner.png",
            "/files",
            "normal",
            now - timedelta(hours=20),
            timedelta(days=1),
        ),
    ]
    for origin, actor, title, body, url, priority, read_at, age in entries:
        module = registry.get(origin)
        notif = Notification.objects.create(
            recipient=alex,
            origin=origin,
            icon=module.icon if module else "bell",
            title=title,
            body=body,
            url=url,
            actor=actor,
            priority=priority,
            read_at=read_at,
        )
        _backdate(notif, now - age)


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
        # Park the cursor: whatever sits under the viewport centre would
        # otherwise be captured hovered (selection ring, row menu).
        page.mouse.move(5, 5)
        page.wait_for_timeout(600)
        # Thumbnails are lazy images fetched one request each; on a slow
        # host the fixed settle leaves half the grid still grey.
        page.wait_for_function(
            "[...document.images].every(img => img.complete && img.naturalWidth > 0)",
            timeout=15000,
        )

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


def _person_panel(page):
    # The panel is fetched after load; wait for the name field to carry
    # the contact rather than trusting the fixed settle.
    page.wait_for_function(
        "document.querySelector('#person-panel input[placeholder=\"Name\"]')?.value",
        timeout=15000,
    )
    # Park the cursor so no row or field is captured hovered.
    page.mouse.move(5, 5)
    page.wait_for_timeout(500)


def _open_notifications(page):
    page.click('button[title="Notifications"]')
    # Park the cursor so no list item is captured hovered.
    page.mouse.move(5, 5)
    page.wait_for_timeout(1500)


SHOTS = [
    {"name": "home", "path": "/"},
    {
        "name": "files_1",
        "path": "/files/{photos_uuid}",
        "prep": _files_view("Mosaic"),
    },
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
    {
        "name": "projects_2",
        "path": "/projects/{project_uuid}/board?task={task_uuid}",
        "settle_ms": 2500,
    },
    {
        "name": "projects_3",
        "path": "/projects/{project_uuid}/timeline?scale=month",
        "settle_ms": 2500,
    },
    {"name": "projects_4", "path": "/projects/{project_uuid}/analytics"},
    {"name": "ai_1", "path": "/chat/{bot_conversation_uuid}"},
    {
        "name": "people_1",
        "path": "/people?person={person_uuid}",
        "prep": _person_panel,
    },
    {"name": "notifications_1", "path": "/", "prep": _open_notifications},
]
