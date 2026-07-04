#!/usr/bin/env python
"""Seed a demo/stress environment for Workspace.

Populates the database with a configurable number of fake users, each owning a
random file tree, a set of calendars with events, plus fake chat conversations
(department groups + direct messages). The goal is a believable "company demo"
dataset that also stresses listings, tree queries, thumbnails, the activity
feed and message rendering under volume.

Human content (names, emails, sentences, paragraphs, addresses) is generated
with `faker` across several locales for an international feel; the app-specific
taxonomy (folders, departments, event titles) stays curated. Users and group
conversations get a locally-generated avatar (colored square + initials, via
Pillow — no network), processed through the app's real avatar services.

All activity is backdated across ``--history-days`` (default 180) so listings,
the calendar and the activity feed/heatmap look lived-in rather than all
timestamped "now". File rows AND their FileEvent CREATED records are backdated
together, since the activity feed orders on FileEvent.created_at.

Data is created through the Django models and the ``files`` service layer (not
the HTTP API), so no running server is required and business logic (storage,
events, mime detection) stays intact.

Usage:
    uv run python scripts/seed_demo.py --users 25
    uv run python scripts/seed_demo.py --users 200 --min-files 20 --max-files 300
    uv run python scripts/seed_demo.py --history-days 365   # spread over a year
    uv run python scripts/seed_demo.py --seed 42            # reproducible run
    uv run python scripts/seed_demo.py --purge --yes        # wipe prior demo data

All seeded users share the ``--email-domain`` (default ``demo.local``) and the
``--password`` (default ``demo1234``). ``--purge`` deletes every user on that
domain and cascades their files/conversations (including the on-disk blobs,
via the File post_delete signal) before (re)seeding.
"""

import argparse
import os
import random
import re
import sys
import unicodedata
from io import BytesIO
from pathlib import Path

import django

# --- Django bootstrap -------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
django.setup()

from datetime import timedelta  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.base import ContentFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402
from faker import Faker  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from workspace.calendar.models import Calendar, Event, EventMember  # noqa: E402
from workspace.chat.models import (  # noqa: E402
    Conversation,
    ConversationMember,
    Message,
)
from workspace.chat.services import avatar as group_avatar_service  # noqa: E402
from workspace.chat.services.conversations import get_or_create_dm  # noqa: E402
from workspace.chat.services.rendering import render_message_body  # noqa: E402
from workspace.files.models import File, FileEvent  # noqa: E402
from workspace.files.services import FileService  # noqa: E402
from workspace.users.models import UserPresence  # noqa: E402
from workspace.users.services import avatar as avatar_service  # noqa: E402

User = get_user_model()

# Multi-locale for an international-company feel (names span several scripts).
# Seeded from --seed in main() for reproducible runs.
fake = Faker(["en_US", "en_GB", "fr_FR", "de_DE", "es_ES", "it_IT", "nl_NL"])


def _slug(value):
    """ASCII, lowercase, dot-separated slug safe for usernames and emails."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", ".", value).strip(".").lower()
    return value or "user"


# --- Fake-data vocabulary ---------------------------------------------------
# Human names / emails / sentences / paragraphs come from faker; the lists
# below are the app-specific structural anchors (folder taxonomy, file stems,
# department names, event titles) that keep the demo reading like a company.

DEPARTMENTS = [
    "Engineering",
    "Sales",
    "Marketing",
    "Human Resources",
    "Finance",
    "Operations",
    "Design",
    "Customer Support",
    "Product",
    "Legal",
]

TOP_FOLDERS = [
    "Documents",
    "Projects",
    "Reports",
    "Archive",
    "Shared",
    "Templates",
    "Meeting Notes",
    "Contracts",
    "Invoices",
    "Presentations",
    "Research",
    "Onboarding",
    "Photos",
    "Budgets",
    "Roadmap",
    "Specs",
]

SUB_FOLDERS = [
    "2023",
    "2024",
    "2025",
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "Drafts",
    "Final",
    "Review",
    "Backup",
    "Assets",
    "Exports",
    "Client A",
    "Client B",
    "Internal",
    "External",
    "Legacy",
    "WIP",
    "Approved",
]

FILE_STEMS = [
    "summary",
    "notes",
    "report",
    "budget",
    "roadmap",
    "spec",
    "proposal",
    "meeting",
    "invoice",
    "contract",
    "todo",
    "changelog",
    "readme",
    "plan",
    "analysis",
    "review",
    "draft",
    "minutes",
    "checklist",
    "overview",
    "estimate",
    "timeline",
    "backlog",
    "retro",
    "metrics",
]

MESSAGE_SNIPPETS = [
    "Hey, can you take a look at the latest draft?",
    "Sure, I'll review it this afternoon.",
    "The deploy went out, everything looks green.",
    "Did we get sign-off from the client yet?",
    "Meeting moved to 3pm, does that work for everyone?",
    "Great work on the release notes!",
    "I pushed a fix for the file upload bug.",
    "Can someone double-check the budget numbers?",
    "Lunch? 🍕",
    "The new onboarding flow is live.",
    "Thanks, that unblocks me.",
    "Let's sync tomorrow morning about the roadmap.",
    "I'll handle the customer follow-up.",
    "Ticket #482 is resolved.",
    "Reminder: quarterly review is next week.",
    "Nice, the numbers are trending up 📈",
    "Can you share the presentation link?",
    "Approved on my end 👍",
    "Heads up, the API rate limits changed.",
    "Coffee break in 5?",
]

CALENDAR_NAMES = ["Personal", "Work", "Team", "Travel", "Family", "Projects"]

# daisyUI theme color slots used by the calendar UI's color picker.
CALENDAR_COLORS = [
    "primary",
    "secondary",
    "accent",
    "info",
    "success",
    "warning",
    "error",
]

EVENT_TITLES = [
    "Team standup",
    "Sprint planning",
    "1:1 with manager",
    "Client call",
    "Design review",
    "Retrospective",
    "All-hands",
    "Product demo",
    "Interview",
    "Lunch & learn",
    "Budget review",
    "Roadmap sync",
    "Onboarding session",
    "Release checkpoint",
    "Coffee chat",
    "Quarterly review",
    "Deadline",
    "Workshop",
    "Kickoff",
    "Sync",
    "Planning",
    "Demo day",
]

EVENT_LOCATIONS = [
    "Meeting Room A",
    "Meeting Room B",
    "Zoom",
    "Google Meet",
    "HQ - 3rd floor",
    "Cafeteria",
    "Remote",
    "Client office",
    "",
]


# --- Avatar generation ------------------------------------------------------
#
# Avatars are generated locally with Pillow (no network) as a colored square
# with centered initials, then handed to the existing avatar services, which
# crop/resize to a 256px WebP and set the presence flag. The color is derived
# from the seed string so a given name/group always gets the same background.

AVATAR_SIZE = 256

AVATAR_PALETTE = [
    (239, 68, 68),
    (249, 115, 22),
    (234, 179, 8),
    (34, 197, 94),
    (16, 185, 129),
    (6, 182, 212),
    (59, 130, 246),
    (99, 102, 241),
    (139, 92, 246),
    (168, 85, 247),
    (217, 70, 239),
    (236, 72, 153),
]


def _avatar_png(initials, seed_str):
    """A square PNG: white initials centered on a color picked from seed_str."""
    color = AVATAR_PALETTE[sum(map(ord, seed_str)) % len(AVATAR_PALETTE)]
    img = Image.new("RGB", (AVATAR_SIZE, AVATAR_SIZE), color)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=120)
    box = draw.textbbox((0, 0), initials, font=font)
    x = (AVATAR_SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (AVATAR_SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), initials, fill="white", font=font)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _text_body(min_chars=300, max_chars=3000):
    return fake.text(max_nb_chars=random.randint(min_chars, max_chars))


def _make_file_content():
    """Return (extension, bytes) for a random small text-based file."""
    kind = random.choice(["md", "txt", "csv", "json", "log", "py"])
    if kind == "csv":
        header = "id,name,email,company,amount,date\n"
        rows = "".join(
            f"{i},{fake.name()},{fake.email()},{fake.company()},"
            f"{random.randint(10, 9999)},{fake.date()}\n"
            for i in range(random.randint(5, 80))
        )
        return "csv", (header + rows).encode()
    if kind == "json":
        items = ", ".join(
            f'{{"id": {i}, "label": "{random.choice(FILE_STEMS)}", '
            f'"owner": "{fake.user_name()}", "value": {random.randint(0, 1000)}}}'
            for i in range(random.randint(3, 30))
        )
        return "json", f'{{"items": [{items}]}}'.encode()
    if kind == "log":
        level = ["INFO", "WARN", "ERROR", "DEBUG"]
        lines = "".join(
            f"{fake.date_time_this_year().isoformat()} {random.choice(level)} "
            f"{fake.sentence()}\n"
            for _ in range(random.randint(10, 120))
        )
        return "log", lines.encode()
    if kind == "py":
        body = "\n".join(
            f"def {random.choice(FILE_STEMS)}_{i}():\n    return {random.randint(0, 99)}"
            for i in range(random.randint(2, 15))
        )
        return "py", f'"""Auto-generated demo module."""\n\n\n{body}\n'.encode()
    if kind == "md":
        return "md", f"# {fake.sentence(nb_words=4)}\n\n{_text_body()}\n".encode()
    return "txt", _text_body().encode()


# --- Backdating helpers -----------------------------------------------------
#
# Every created_at/updated_at in the schema is auto_now_add / auto_now, so the
# value is forced at INSERT / SAVE. To spread activity over time we must rewrite
# the column with a bulk ``queryset.update(...)`` (which bypasses the auto flags)
# AFTER creation. Crucially, backdating a File also requires backdating its
# FileEvent CREATED row — the activity feed + heatmap order/display by
# FileEvent.created_at, so leaving it at "now" makes an old file look brand new.


def _rand_past(history_days, until=None):
    """A tz-aware datetime uniformly in [now - history_days, until|now]."""
    end = until or timezone.now()
    return end - timedelta(days=random.random() * history_days)


def _sorted_times(n, start, end):
    """``n`` tz-aware datetimes sorted ascending within [start, end]."""
    span = (end - start).total_seconds()
    return sorted(start + timedelta(seconds=random.random() * span) for _ in range(n))


def _backdate_file(file_obj, ts):
    """Rewrite a File's created/updated AND its FileEvent rows to ``ts``."""
    File.objects.filter(pk=file_obj.pk).update(created_at=ts, updated_at=ts)
    FileEvent.objects.filter(file=file_obj).update(created_at=ts)


# --- Seeding logic ----------------------------------------------------------


def create_users(count, domain, password, avatar_ratio):
    """Create ``count`` demo users, their presence rows and avatars.

    Returns (users, n_avatars). ``avatar_ratio`` (0..1) is the share of users
    that get a generated avatar; the rest fall back to the app's default
    initials rendering.
    """
    users, n_avatars = [], 0
    for i in range(count):
        first = fake.first_name()
        last = fake.last_name()
        # ASCII slug keeps the email/username valid across faker's locales
        # (accents, spaces). Short random suffix keeps them varied; re-draw on
        # the rare collision (fixed --seed re-run against existing rows).
        base = f"{_slug(first)}.{_slug(last)}"
        username = f"{base}.{random.randint(0, 0xFFFF):04x}"
        while User.objects.filter(username=username).exists():
            username = f"{base}.{random.randint(0, 0xFFFFFF):06x}"
        user = User.objects.create_user(
            username=username,
            email=f"{username}@{domain}",
            first_name=first,
            last_name=last,
            password=password,
            is_active=True,
        )
        UserPresence.objects.get_or_create(
            user=user, defaults={"last_seen": timezone.now()}
        )
        if random.random() < avatar_ratio:
            initials = (_slug(first)[:1] + _slug(last)[:1]).upper() or "U"
            png = _avatar_png(initials, username)
            avatar_service.process_and_save_avatar(
                user, BytesIO(png), 0, 0, AVATAR_SIZE, AVATAR_SIZE
            )
            n_avatars += 1
        users.append(user)
        print(f"  user {i + 1}/{count}: {username}", flush=True)
    return users, n_avatars


def build_file_tree(user, min_files, max_files, max_depth, history_days):
    """Create a random folder tree + files for one user. Returns file count.

    Every folder/file (and its CREATED FileEvent) is backdated to a random
    point in the last ``history_days`` so listings and the activity feed look
    lived-in. A folder is stamped no later than the files placed inside it, so
    a child never predates its parent.
    """
    n_files = random.randint(min_files, max_files)
    n_folders = max(1, random.randint(n_files // 6, max(1, n_files // 2)))

    # (folder_or_None, depth, created_ts) placement targets; None == user root.
    root_ts = _rand_past(history_days)
    targets = [(None, 0, root_ts)]
    top_pool = random.sample(
        TOP_FOLDERS, k=min(len(TOP_FOLDERS), max(2, n_folders // 3))
    )

    with transaction.atomic():
        for name in top_pool:
            ts = _rand_past(history_days)
            folder = FileService.create_folder(owner=user, name=name)
            _backdate_file(folder, ts)
            targets.append((folder, 1, ts))

        for _ in range(n_folders):
            parent, depth, parent_ts = random.choice(targets)
            if depth >= max_depth:
                continue
            name = random.choice(SUB_FOLDERS)
            ts = _rand_past(history_days, until=timezone.now())
            ts = max(ts, parent_ts)  # never predate the parent folder
            folder = FileService.create_folder(owner=user, name=name, parent=parent)
            _backdate_file(folder, ts)
            targets.append((folder, depth + 1, ts))

        for _ in range(n_files):
            parent, _depth, parent_ts = random.choice(targets)
            ext, data = _make_file_content()
            stem = random.choice(FILE_STEMS)
            name = f"{stem}-{random.randint(1, 999)}.{ext}"
            f = FileService.create_file(
                owner=user,
                name=name,
                parent=parent,
                content=ContentFile(data, name=name),
            )
            ts = max(_rand_past(history_days), parent_ts)
            _backdate_file(f, ts)
    return n_files


def _post_messages(conversation, members, min_msgs, max_msgs, history_days):
    n = random.randint(min_msgs, max_msgs)
    active = [m.user if isinstance(m, ConversationMember) else m for m in members]

    # A conversation lives in a random sub-window of the history; its messages
    # are backdated in chronological order inside it. created_at/updated_at are
    # auto flags, so rewrite them with .update() to control chat-list ordering.
    start = _rand_past(history_days)
    end = _rand_past(history_days / 4, until=timezone.now())
    if end < start:
        start, end = end, start
    times = _sorted_times(n, start, end)

    for ts in times:
        author = random.choice(active)
        # Mix curated workplace one-liners with faker sentences for variety.
        body = (
            random.choice(MESSAGE_SNIPPETS)
            if random.random() < 0.5
            else fake.sentence()
        )
        msg = Message.objects.create(
            conversation=conversation,
            author=author,
            body=body,
            # The chat UI renders body_html, not body; the real send path fills
            # it via render_message_body. Skip it and messages show up blank.
            body_html=render_message_body(body),
        )
        Message.objects.filter(pk=msg.pk).update(created_at=ts)

    last = times[-1] if times else end
    Conversation.objects.filter(pk=conversation.pk).update(
        created_at=start, updated_at=last
    )
    return n


def create_group_conversations(users, min_msgs, max_msgs, history_days, avatar_ratio):
    """One group conversation per department, filled with random members.

    Returns (n_convs, n_messages, n_avatars).
    """
    convs, total_msgs, n_avatars = 0, 0, 0
    if len(users) < 2:
        return convs, total_msgs, n_avatars

    # Assign each user to a department, then build a group per non-trivial dept.
    buckets = {d: [] for d in DEPARTMENTS}
    for u in users:
        buckets[random.choice(DEPARTMENTS)].append(u)

    for dept, members in buckets.items():
        if len(members) < 2:
            continue
        creator = members[0]
        with transaction.atomic():
            conv = Conversation.objects.create(
                kind=Conversation.Kind.GROUP,
                title=dept,
                created_by=creator,
            )
            ConversationMember.objects.bulk_create(
                [ConversationMember(conversation=conv, user=m) for m in members]
            )
            total_msgs += _post_messages(
                conv, members, min_msgs, max_msgs, history_days
            )
            if random.random() < avatar_ratio:
                initials = "".join(w[:1] for w in dept.split()[:2]).upper()
                png = _avatar_png(initials, dept)
                group_avatar_service.process_and_save_group_avatar(
                    conv, BytesIO(png), 0, 0, AVATAR_SIZE, AVATAR_SIZE
                )
                n_avatars += 1
        convs += 1
        print(f"  group '{dept}' ({len(members)} members)", flush=True)
    return convs, total_msgs, n_avatars


def create_dms(users, count, min_msgs, max_msgs, history_days):
    """Create ``count`` random direct-message conversations with messages."""
    convs, total_msgs = 0, 0
    if len(users) < 2:
        return convs, total_msgs
    for _ in range(count):
        a, b = random.sample(users, 2)
        conv = get_or_create_dm(a, b)
        with transaction.atomic():
            total_msgs += _post_messages(conv, [a, b], min_msgs, max_msgs, history_days)
        convs += 1
    return convs, total_msgs


def create_calendars_and_events(user, others, min_events, max_events, history_days):
    """Give ``user`` 1-3 owned calendars with random events spread over time.

    Events fall in [now - history_days, now + 30d] so the calendar shows both
    past and upcoming entries. ~30% are shared: a few random ``others`` are
    invited via EventMember. Returns (n_calendars, n_events).
    """
    # Match the lazy default-calendar guard used by the UI so we never create a
    # second "Personal" calendar for a user who already has one.
    calendars = []
    if not Calendar.objects.filter(owner=user).exists():
        calendars.append(
            Calendar.objects.create(owner=user, name="Personal", color="primary")
        )
    extra = random.sample(
        [n for n in CALENDAR_NAMES if n != "Personal"], k=random.randint(0, 2)
    )
    for name in extra:
        calendars.append(
            Calendar.objects.create(
                owner=user, name=name, color=random.choice(CALENDAR_COLORS)
            )
        )
    if not calendars:  # user already had calendars and drew 0 extra
        calendars = list(Calendar.objects.filter(owner=user))

    n_events = random.randint(min_events, max_events)
    for _ in range(n_events):
        _create_event(user, random.choice(calendars), others, history_days)
    return len(calendars), n_events


def _create_event(user, cal, others, history_days):
    """Create one random event in ``cal`` (past or upcoming), maybe shared."""
    # start anywhere from history_days ago to 30 days ahead.
    offset = random.uniform(-history_days, 30)
    start = timezone.now() + timedelta(days=offset, hours=random.uniform(0, 23))
    all_day = random.random() < 0.15
    if all_day:
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=random.randint(1, 3))
    else:
        end = start + timedelta(minutes=random.choice([30, 45, 60, 90, 120]))

    title = (
        random.choice(EVENT_TITLES) if random.random() < 0.7 else fake.catch_phrase()
    )
    event = Event.objects.create(
        calendar=cal,
        owner=user,
        title=title,
        description=fake.paragraph() if random.random() < 0.5 else "",
        location=random.choice(EVENT_LOCATIONS),
        start=start,
        end=end,
        all_day=all_day,
        recurrence_frequency=(
            random.choice(["daily", "weekly", "monthly"])
            if random.random() < 0.15
            else None
        ),
    )
    # Backdate created_at (auto_now_add) to a plausible "scheduled on" time.
    Event.objects.filter(pk=event.pk).update(created_at=_rand_past(history_days))

    if others and random.random() < 0.3:
        invited = random.sample(others, k=min(len(others), random.randint(1, 3)))
        EventMember.objects.bulk_create(
            [EventMember(event=event, user=u) for u in invited if u != user]
        )


def purge(domain, assume_yes):
    """Delete all users on ``domain`` and cascade their data."""
    qs = User.objects.filter(email__endswith=f"@{domain}")
    n = qs.count()
    if not n:
        print(f"No existing users on @{domain} to purge.")
        return
    if not assume_yes:
        answer = input(f"Delete {n} users on @{domain} and ALL their data? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            sys.exit(1)
    # Avatar webp blobs live in storage outside the ORM cascade, so remove them
    # explicitly — otherwise deleting the rows orphans the files (and since user
    # ids change every run, they would accumulate on disk).
    for user in qs:
        avatar_service.delete_avatar(user)
    for conv in Conversation.objects.filter(created_by__in=qs, has_avatar=True):
        group_avatar_service.delete_group_avatar(conv)
    deleted, _ = qs.delete()
    print(f"Purged {n} users on @{domain} ({deleted} rows total).")


def main():
    parser = argparse.ArgumentParser(
        description="Seed a demo/stress dataset (users, files, conversations).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--users", type=int, default=10, help="number of users to create"
    )
    parser.add_argument("--min-files", type=int, default=5, help="min files per user")
    parser.add_argument("--max-files", type=int, default=50, help="max files per user")
    parser.add_argument(
        "--max-depth", type=int, default=4, help="max folder nesting depth"
    )
    parser.add_argument(
        "--dms",
        type=int,
        default=None,
        help="number of DM conversations (default: = users)",
    )
    parser.add_argument(
        "--min-messages", type=int, default=3, help="min messages per conversation"
    )
    parser.add_argument(
        "--max-messages", type=int, default=30, help="max messages per conversation"
    )
    parser.add_argument(
        "--min-events", type=int, default=5, help="min calendar events per user"
    )
    parser.add_argument(
        "--max-events", type=int, default=40, help="max calendar events per user"
    )
    parser.add_argument(
        "--history-days",
        type=int,
        default=180,
        help="spread activity over the last N days",
    )
    parser.add_argument(
        "--password", default="demo1234", help="password for all seeded users"
    )
    parser.add_argument(
        "--email-domain", default="demo.local", help="email domain marking demo users"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="RNG seed for reproducible runs"
    )
    parser.add_argument(
        "--no-files", action="store_true", help="skip file-tree generation"
    )
    parser.add_argument(
        "--no-chat", action="store_true", help="skip conversation generation"
    )
    parser.add_argument(
        "--no-calendar", action="store_true", help="skip calendar/event generation"
    )
    parser.add_argument(
        "--purge", action="store_true", help="delete existing demo users first"
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip purge confirmation prompt"
    )
    parser.add_argument(
        "--no-avatars", action="store_true", help="skip generated avatars"
    )
    parser.add_argument(
        "--avatar-ratio",
        type=float,
        default=0.85,
        help="share of users/groups that get a generated avatar (0..1)",
    )
    args = parser.parse_args()

    if args.min_files > args.max_files:
        parser.error("--min-files must be <= --max-files")
    if args.min_messages > args.max_messages:
        parser.error("--min-messages must be <= --max-messages")
    if args.min_events > args.max_events:
        parser.error("--min-events must be <= --max-events")

    if args.seed is not None:
        random.seed(args.seed)
        Faker.seed(args.seed)

    if args.purge:
        purge(args.email_domain, args.yes)

    avatar_ratio = 0.0 if args.no_avatars else args.avatar_ratio

    print(f"\nCreating {args.users} users on @{args.email_domain} ...")
    users, user_avatars = create_users(
        args.users, args.email_domain, args.password, avatar_ratio
    )

    total_files = 0
    if not args.no_files:
        print("\nGenerating file trees ...")
        for user in users:
            n = build_file_tree(
                user, args.min_files, args.max_files, args.max_depth, args.history_days
            )
            total_files += n
            print(f"  {user.username}: {n} files", flush=True)

    calendars = events = 0
    if not args.no_calendar:
        print("\nGenerating calendars & events ...")
        for user in users:
            others = [u for u in users if u != user]
            c, e = create_calendars_and_events(
                user, others, args.min_events, args.max_events, args.history_days
            )
            calendars += c
            events += e
            print(f"  {user.username}: {c} calendars, {e} events", flush=True)

    groups = dms = msgs = group_avatars = 0
    if not args.no_chat:
        print("\nGenerating conversations ...")
        g, gm, ga = create_group_conversations(
            users, args.min_messages, args.max_messages, args.history_days, avatar_ratio
        )
        dm_count = args.dms if args.dms is not None else args.users
        d, dmm = create_dms(
            users, dm_count, args.min_messages, args.max_messages, args.history_days
        )
        groups, dms, msgs, group_avatars = g, d, gm + dmm, ga

    print("\n--- Done ---")
    print(f"  users:         {len(users)}")
    print(f"  avatars:       {user_avatars} user + {group_avatars} group")
    print(f"  files:         {total_files}")
    print(f"  calendars:     {calendars}")
    print(f"  events:        {events}")
    print(f"  group convs:   {groups}")
    print(f"  direct convs:  {dms}")
    print(f"  messages:      {msgs}")
    print(f"\n  Activity spread over the last {args.history_days} days.")
    print(f"  Login with any user's email and password '{args.password}'.")


if __name__ == "__main__":
    main()
