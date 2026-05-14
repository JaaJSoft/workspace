# LLM Event Extraction from Mail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a new email is synced, run an LLM extraction pass to detect concrete scheduled events (meetings, ticket bookings, appointments), create them as calendar `Event` rows mirroring the ICS-invitation pattern, and surface them in the mail-reading UI with a dismiss action.

**Architecture:** Hook a new `AITask.TaskType.EXTRACT` worker into the existing post-sync trigger that already dispatches the classify task. Reuse the calendar's invitation-routing pattern by extracting `Event` creation into a shared service used by both ICS and LLM paths. Track extractions in a new polymorphic `MailExtraction` model (1-N from `MailMessage`, target = `GenericForeignKey` to any extracted thing).

**Tech Stack:** Django 5.x, Celery, OpenAI-compatible client, Pydantic v2, Alpine.js + alpine-ajax, PostgreSQL/SQLite, Redis cache, `django.contrib.contenttypes` (already in `INSTALLED_APPS`).

**Spec reference:** `docs/superpowers/specs/2026-05-14-llm-event-extraction-from-mail-design.md`.

**Single PR, 4 commits.** Each phase ends in one commit. Tests are green at every commit boundary.

---

## Phase 1: Capture In-Reply-To headers (commit 1)

Provides the thread context the LLM needs. Self-contained: no downstream code reads `in_reply_to` until Phase 4, so this phase merges cleanly even if Phase 4 ships later.

### Task 1.1: Add `MailMessage.in_reply_to` field

**Files:**
- Modify: `workspace/mail/models.py:185-186` (insert after `message_id` field)
- Create: `workspace/mail/migrations/0016_mailmessage_in_reply_to.py` (auto-generated)

- [ ] **Step 1: Add the field on the model**

In `workspace/mail/models.py`, locate the `MailMessage` class. After `message_id = models.CharField(max_length=512, blank=True, default='')` add:

```python
    in_reply_to = models.CharField(max_length=512, blank=True, default='')
```

- [ ] **Step 2: Generate the migration**

Run: `uv run python manage.py makemigrations mail`
Expected: a file named `workspace/mail/migrations/0016_mailmessage_in_reply_to.py` is created.

- [ ] **Step 3: Apply the migration locally**

Run: `uv run python manage.py migrate mail`
Expected: `Applying mail.0016_mailmessage_in_reply_to... OK`.

- [ ] **Step 4: Sanity check**

Run: `uv run python manage.py shell -c "from workspace.mail.models import MailMessage; m = MailMessage(); print(m.in_reply_to)"`
Expected: prints an empty string with no error.

---

### Task 1.2: Update the IMAP parser to read `In-Reply-To`

**Files:**
- Modify: `workspace/mail/services/imap_parse.py:113` (existing header parsing block)
- Modify: same file, the `MailMessage.objects.create(...)` call further down
- Test: `workspace/mail/tests/test_imap_parse.py` (file exists; verify structure first)

- [ ] **Step 1: Locate the parser test file structure**

Run: `ls workspace/mail/tests/ | grep parse`
Expected: a file named `test_imap_parse.py` exists. If not, create it with a `class ImapParseTests(TestCase)` skeleton importing `_parse_message` from `workspace.mail.services.imap_parse`.

- [ ] **Step 2: Write a failing test for `in_reply_to` capture**

Add to `workspace/mail/tests/test_imap_parse.py`:

```python
def test_in_reply_to_header_is_captured(self):
    """A raw email with In-Reply-To: <parent@example.com> must persist
    that value to MailMessage.in_reply_to."""
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Re: Coffee?\r\n"
        b"Message-ID: <child@example.com>\r\n"
        b"In-Reply-To: <parent@example.com>\r\n"
        b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
        b"\r\n"
        b"Sure, 3pm works.\r\n"
    )
    msg = _parse_message(
        account=self.account, folder=self.folder,
        imap_uid=42, raw_email=raw,
    )
    self.assertEqual(msg.in_reply_to, '<parent@example.com>')

def test_in_reply_to_absent_defaults_to_empty(self):
    raw = (
        b"From: alice@example.com\r\n"
        b"Message-ID: <solo@example.com>\r\n"
        b"Date: Thu, 14 May 2026 10:00:00 +0000\r\n"
        b"\r\n"
        b"Body\r\n"
    )
    msg = _parse_message(
        account=self.account, folder=self.folder,
        imap_uid=43, raw_email=raw,
    )
    self.assertEqual(msg.in_reply_to, '')
```

- [ ] **Step 3: Run tests, verify both fail**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_imap_parse -v 2`
Expected: both new tests FAIL with `AttributeError` or assertion mismatch (the parser doesn't read the header yet).

- [ ] **Step 4: Read the header in the parser**

In `workspace/mail/services/imap_parse.py` near line 113 where `message_id = msg.get('Message-ID', '')` lives, add right below:

```python
    in_reply_to = msg.get('In-Reply-To', '')
```

- [ ] **Step 5: Wire it into the model create call**

Locate the `MailMessage.objects.create(...)` call further down in the same function. Add `in_reply_to=in_reply_to,` to the kwargs (alphabetically near `imap_uid`).

- [ ] **Step 6: Run tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_imap_parse -v 2`
Expected: both new tests PASS, all other parser tests still PASS.

---

### Task 1.3: Add `get_thread()` helper

**Files:**
- Create: `workspace/mail/services/threads.py`
- Create: `workspace/mail/tests/test_threads.py`

- [ ] **Step 1: Write failing tests for `get_thread()`**

Create `workspace/mail/tests/test_threads.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.threads import get_thread

User = get_user_model()


class GetThreadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='t', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='t@example.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )

    def _make(self, uid, message_id, in_reply_to=''):
        return MailMessage.objects.create(
            account=self.account, folder=self.folder,
            imap_uid=uid, message_id=message_id,
            in_reply_to=in_reply_to, date=timezone.now(),
        )

    def test_solo_message_returns_itself(self):
        m = self._make(1, '<a@x>')
        self.assertEqual(get_thread(m), [m])

    def test_two_message_chain_returned_in_order(self):
        parent = self._make(1, '<a@x>')
        child = self._make(2, '<b@x>', in_reply_to='<a@x>')
        self.assertEqual(get_thread(child), [parent, child])

    def test_three_level_chain(self):
        a = self._make(1, '<a@x>')
        b = self._make(2, '<b@x>', in_reply_to='<a@x>')
        c = self._make(3, '<c@x>', in_reply_to='<b@x>')
        self.assertEqual(get_thread(c), [a, b, c])

    def test_broken_chain_returns_what_we_have(self):
        # Parent referenced but not in DB.
        c = self._make(3, '<c@x>', in_reply_to='<missing@x>')
        self.assertEqual(get_thread(c), [c])

    def test_max_depth_caps_walk(self):
        prev_id = ''
        msgs = []
        for i in range(30):
            mid = f'<m{i}@x>'
            m = self._make(i + 1, mid, in_reply_to=prev_id)
            msgs.append(m)
            prev_id = mid
        # Walk from the newest with max_depth=5: we get 5 messages total
        # (the latest plus 4 ancestors).
        result = get_thread(msgs[-1], max_depth=5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[-1], msgs[-1])

    def test_walk_scoped_to_same_account(self):
        # A parent with the same message_id but in a different account
        # must NOT be returned.
        other_user = User.objects.create_user(username='u2', password='p')
        other_acc = MailAccount.objects.create(
            owner=other_user, email='u2@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        other_folder = MailFolder.objects.create(
            account=other_acc, name='INBOX', folder_type='inbox',
        )
        MailMessage.objects.create(
            account=other_acc, folder=other_folder, imap_uid=1,
            message_id='<a@x>', in_reply_to='', date=timezone.now(),
        )
        child = self._make(2, '<b@x>', in_reply_to='<a@x>')
        self.assertEqual(get_thread(child), [child])
```

- [ ] **Step 2: Run tests, verify they fail (module not found)**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_threads -v 2`
Expected: all tests FAIL with `ModuleNotFoundError: workspace.mail.services.threads`.

- [ ] **Step 3: Implement `get_thread()`**

Create `workspace/mail/services/threads.py`:

```python
"""Reconstruct an email thread by walking In-Reply-To upward.

Used by the LLM event-extraction worker to feed the model the
full conversation context. Capped at max_depth so a pathological
500-reply chain does not silently multiply the LLM bill.
"""

from ..models import MailMessage


def get_thread(message: MailMessage, max_depth: int = 20) -> list[MailMessage]:
    """Return ancestors of `message` in chronological order, ending
    with `message` itself.

    The walk starts from `message.in_reply_to`, looks up a MailMessage
    in the same account whose `message_id` matches, then continues
    upward until in_reply_to is empty, no parent matches in our DB,
    or max_depth ancestors have been collected.

    A solo message (no in_reply_to, or in_reply_to points to an
    unknown id) returns [message]. The thread is always at least
    `message` itself.
    """
    chain = [message]
    current = message
    while (
        len(chain) <= max_depth
        and current.in_reply_to
    ):
        parent = (
            MailMessage.objects
            .filter(account=message.account, message_id=current.in_reply_to)
            .first()
        )
        if parent is None or parent.pk == current.pk:
            break
        chain.append(parent)
        current = parent

    chain.reverse()
    return chain
```

- [ ] **Step 4: Run tests, verify all pass**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_threads -v 2`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit Phase 1**

```bash
git add workspace/mail/models.py workspace/mail/migrations/0016_mailmessage_in_reply_to.py workspace/mail/services/imap_parse.py workspace/mail/services/threads.py workspace/mail/tests/test_imap_parse.py workspace/mail/tests/test_threads.py docs/superpowers/specs/2026-05-14-llm-event-extraction-from-mail-design.md docs/superpowers/plans/2026-05-14-llm-event-extraction-from-mail.md
git commit -m "feat(mail): store In-Reply-To header on incoming messages"
```

The spec + plan are committed alongside the first phase so the design lives in git from the start of the feature branch.

---

## Phase 2: Extract shared event-creation service (commit 2)

Pre-requisite: confirm existing ICS tests cover `_create_event` end-to-end before the move. If coverage is thin, write tests first - the refactor is otherwise blind.

### Task 2.1: Pre-refactor coverage audit

**Files:**
- Read-only: `workspace/calendar/tests/test_ics_processor.py` (verify exists, list tests)
- Possibly create: `workspace/calendar/tests/test_ics_processor.py::test_create_event_*` if coverage is thin

- [ ] **Step 1: List existing ICS test coverage**

Run: `grep -n "^    def test_" workspace/calendar/tests/test_ics_processor.py`
Expected: a list of test names. Confirm at least these are covered (test names may vary; cover the behavior):
1. New ICS invitation creates an Event with the right fields.
2. New ICS invitation creates a `EventMember(status=PENDING)`.
3. ICS invitation routes to the `mail_account`'s invitation calendar (`_get_or_create_invitation_calendar`).
4. ICS UPDATE (higher SEQUENCE) updates the existing event.
5. ICS CANCEL flips `is_cancelled=True`.

- [ ] **Step 2: If any of those 5 are missing, write the test now**

Use the existing test file's style. The goal is to lock down current behavior so the refactor in Task 2.2 has a safety net. If all 5 are covered, this step is a no-op.

- [ ] **Step 3: Run the full calendar test suite to confirm baseline green**

Run: `uv run --no-sync python manage.py test workspace.calendar -v 2 2>&1 | tail -5`
Expected: all tests PASS. Note the count - we will re-check after each refactor task that the same count still passes.

---

### Task 2.2: Create `event_creation.py` with the shared helper

**Files:**
- Create: `workspace/calendar/services/event_creation.py`
- Create: `workspace/calendar/tests/test_event_creation.py`

- [ ] **Step 1: Write failing tests for `create_event_from_payload`**

Create `workspace/calendar/tests/test_event_creation.py`:

```python
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.calendar.services.event_creation import (
    create_event_from_payload, get_or_create_invitation_calendar,
)
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class CreateEventFromPayloadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='c', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='c@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            message_id='<m@x>', date=datetime.now(timezone.utc),
        )

    def _payload(self, **overrides):
        base = {
            'title': 'Lunch',
            'start': datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            'end': datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
            'all_day': False,
            'location': 'Cafe',
            'description': '',
        }
        base.update(overrides)
        return base

    def test_creates_event_with_payload_fields(self):
        event = create_event_from_payload(
            user=self.user, payload=self._payload(),
            source_message=self.message,
        )
        self.assertEqual(event.title, 'Lunch')
        self.assertEqual(event.location, 'Cafe')
        self.assertEqual(event.owner, self.user)
        self.assertEqual(event.source_message, self.message)

    def test_routes_to_invitation_calendar(self):
        event = create_event_from_payload(
            user=self.user, payload=self._payload(),
            source_message=self.message,
        )
        self.assertEqual(event.calendar.mail_account, self.account)

    def test_get_or_create_invitation_calendar_is_idempotent(self):
        first = get_or_create_invitation_calendar(self.account)
        second = get_or_create_invitation_calendar(self.account)
        self.assertEqual(first.pk, second.pk)

    def test_optional_ical_uid_and_organizer(self):
        event = create_event_from_payload(
            user=self.user, payload=self._payload(),
            source_message=self.message,
            ical_uid='ABC-123', external_organizer='alice@x.com',
        )
        self.assertEqual(event.ical_uid, 'ABC-123')
        self.assertEqual(event.external_organizer, 'alice@x.com')

    def test_defaults_when_ical_fields_omitted(self):
        event = create_event_from_payload(
            user=self.user, payload=self._payload(),
            source_message=self.message,
        )
        self.assertIn(event.ical_uid, (None, ''))
        self.assertIn(event.external_organizer, (None, ''))
        self.assertEqual(event.ical_sequence, 0)
```

- [ ] **Step 2: Run tests, verify they fail (module not found)**

Run: `uv run --no-sync python manage.py test workspace.calendar.tests.test_event_creation -v 2`
Expected: tests FAIL with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement `event_creation.py`**

Create `workspace/calendar/services/event_creation.py`. The function bodies copy the existing logic from `ics_processor._create_event` and `_get_or_create_invitation_calendar` - they will be replaced inline in Task 2.3 with calls to these new functions.

```python
"""Shared Event-creation primitives used by both ICS processing and
LLM extraction. Centralises the routing-to-invitation-calendar logic
and the Event-row creation so the two source paths stay in sync.
"""

from django.db import transaction

from workspace.calendar.models import Calendar, Event


def get_or_create_invitation_calendar(account):
    """Return the calendar that hosts mail-sourced events for `account`,
    creating it on first use. Kept verbatim from the previous ICS-only
    implementation so behaviour is unchanged."""
    calendar = Calendar.objects.filter(mail_account=account).first()
    if calendar:
        expected_name = account.display_name or account.email
        if calendar.name != expected_name:
            calendar.name = expected_name
            calendar.save(update_fields=['name', 'updated_at'])
        return calendar

    return Calendar.objects.create(
        name=account.display_name or account.email,
        color='secondary',
        owner=account.owner,
        mail_account=account,
    )


@transaction.atomic
def create_event_from_payload(
    *,
    user,
    payload: dict,
    source_message=None,
    ical_uid: str = '',
    ical_sequence: int = 0,
    external_organizer: str = '',
) -> Event:
    """Create an Event from a normalised payload.

    payload keys: title (str), start (datetime, required, tz-aware),
    end (datetime or None), all_day (bool), location (str),
    description (str).

    source_message links the event back to the mail that produced it
    (ICS attachment OR LLM extraction). For native events this is None.
    """
    calendar = (
        get_or_create_invitation_calendar(source_message.account)
        if source_message else None
    )
    if calendar is None:
        raise ValueError(
            'create_event_from_payload currently requires a source_message; '
            'native (calendar-only) creation has its own code path'
        )

    return Event.objects.create(
        calendar=calendar,
        title=payload['title'],
        description=payload.get('description', ''),
        start=payload['start'],
        end=payload.get('end'),
        all_day=payload.get('all_day', False),
        location=payload.get('location', ''),
        owner=user,
        ical_uid=ical_uid or None,
        ical_sequence=ical_sequence,
        external_organizer=external_organizer or None,
        source_message=source_message,
    )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.calendar.tests.test_event_creation -v 2`
Expected: 5 tests PASS.

---

### Task 2.3: Migrate `ics_processor._create_event` to use the shared service

**Files:**
- Modify: `workspace/calendar/services/ics_processor.py:83-129` (replace `_create_event`)
- Modify: `workspace/calendar/services/ics_processor.py:158-173` (delete `_get_or_create_invitation_calendar`)
- Modify: `workspace/calendar/services/ics_processor.py:1-12` (imports)

- [ ] **Step 1: Replace `_create_event` body**

In `workspace/calendar/services/ics_processor.py`, replace the entire `_create_event` function (lines 83-129) with:

```python
def _create_event(vevent, uid, sequence, mail_message):
    """Create a new Event from a VEVENT component."""
    from workspace.calendar.services.event_creation import create_event_from_payload

    user = mail_message.account.owner
    external_organizer = _extract_email(vevent.get('ORGANIZER'))
    dtstart = _to_datetime(vevent.get('DTSTART'))
    dtend = _to_datetime(vevent.get('DTEND'))

    event = create_event_from_payload(
        user=user,
        payload={
            'title': str(vevent.get('SUMMARY', '')),
            'description': str(vevent.get('DESCRIPTION', '')),
            'start': dtstart,
            'end': dtend,
            'all_day': _is_all_day(vevent.get('DTSTART')),
            'location': str(vevent.get('LOCATION', '')),
        },
        source_message=mail_message,
        ical_uid=uid,
        ical_sequence=sequence,
        external_organizer=external_organizer,
    )

    EventMember.objects.create(
        event=event,
        user=user,
        status=EventMember.Status.PENDING,
    )

    if _is_future_event(event):
        notify(
            recipient=user,
            origin='calendar',
            title=f'Invitation: {event.title}',
            body=f'From {external_organizer}',
            url=f'/calendar?event={event.pk}',
        )

    return event
```

- [ ] **Step 2: Delete the now-dead `_get_or_create_invitation_calendar`**

Remove lines 158-173 (the local definition) from `workspace/calendar/services/ics_processor.py`. The function lives in `event_creation.py` now and is no longer referenced from this file.

- [ ] **Step 3: Clean up unused imports**

If `Calendar` is no longer referenced in `ics_processor.py`, remove it from the `from workspace.calendar.models import Calendar, Event, EventMember` line. Same for `transaction` if no other call sites use it.

- [ ] **Step 4: Run the full calendar test suite**

Run: `uv run --no-sync python manage.py test workspace.calendar -v 2 2>&1 | tail -10`
Expected: same number of tests PASS as in Task 2.1 Step 3. Any regression here means the refactor changed behaviour.

- [ ] **Step 5: Commit Phase 2**

```bash
git add workspace/calendar/services/event_creation.py workspace/calendar/services/ics_processor.py workspace/calendar/tests/test_event_creation.py
git commit -m "refactor(calendar): extract event creation into shared service"
```

---

## Phase 3: Add `Event.source` discriminator (commit 3)

### Task 3.1: Add `Event.source` field + data migration

**Files:**
- Modify: `workspace/calendar/models.py:66` (add `Source` choices class and `source` field)
- Create: `workspace/calendar/migrations/0015_event_source.py` (manually edit auto-gen to add data migration)
- Modify: `workspace/calendar/services/event_creation.py` (pass `source` through)
- Modify: `workspace/calendar/services/ics_processor.py` (use `Event.Source.ICS`)
- Modify: `workspace/calendar/tests/test_event_creation.py` (add source assertions)

- [ ] **Step 1: Write a failing test for source on LLM-path event creation**

Add to `workspace/calendar/tests/test_event_creation.py`:

```python
    def test_source_defaults_to_manual_when_not_set(self):
        event = create_event_from_payload(
            user=self.user, payload=self._payload(),
            source_message=self.message,
        )
        self.assertEqual(event.source, Event.Source.MANUAL)

    def test_source_can_be_set_to_llm(self):
        event = create_event_from_payload(
            user=self.user, payload=self._payload(),
            source_message=self.message,
            source=Event.Source.LLM,
        )
        self.assertEqual(event.source, Event.Source.LLM)
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `uv run --no-sync python manage.py test workspace.calendar.tests.test_event_creation.CreateEventFromPayloadTests.test_source_defaults_to_manual_when_not_set -v 2`
Expected: FAIL with `AttributeError: type object 'Event' has no attribute 'Source'`.

- [ ] **Step 3: Add the `Source` choices class and `source` field**

In `workspace/calendar/models.py:66` (the `Event` class), add right below the `RecurrenceFrequency` inner class:

```python
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        ICS = 'ics', 'ICS invitation'
        LLM = 'llm', 'Extracted by AI'
```

Then near the other model fields (alphabetical or grouped by concern; place near `external_organizer` since it is also "where did this event come from?"), add:

```python
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.MANUAL,
    )
```

- [ ] **Step 4: Generate the schema migration**

Run: `uv run python manage.py makemigrations calendar`
Expected: `workspace/calendar/migrations/0015_event_source.py` created.

- [ ] **Step 5: Add the data backfill to the migration**

Open the new migration file and add a `RunPython` step after the schema operation. The final file should look like:

```python
from django.db import migrations, models


def backfill_source(apps, schema_editor):
    Event = apps.get_model('calendar', 'Event')
    # Anything with an ical_uid was created from an ICS attachment.
    Event.objects.filter(ical_uid__isnull=False).exclude(ical_uid='').update(source='ics')


class Migration(migrations.Migration):

    dependencies = [
        ('calendar', '0014_partial_scheduled_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='source',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('ics', 'ICS invitation'), ('llm', 'Extracted by AI')],
                default='manual', max_length=16,
            ),
        ),
        migrations.RunPython(backfill_source, migrations.RunPython.noop),
    ]
```

- [ ] **Step 6: Apply the migration**

Run: `uv run python manage.py migrate calendar`
Expected: `Applying calendar.0015_event_source... OK`.

- [ ] **Step 7: Thread `source` through `create_event_from_payload`**

In `workspace/calendar/services/event_creation.py`, change the signature and the create call:

```python
@transaction.atomic
def create_event_from_payload(
    *,
    user,
    payload: dict,
    source: str = '',  # Event.Source.* - empty string falls through to the model default
    source_message=None,
    ical_uid: str = '',
    ical_sequence: int = 0,
    external_organizer: str = '',
) -> Event:
    ...
    create_kwargs = dict(
        calendar=calendar,
        title=payload['title'],
        description=payload.get('description', ''),
        start=payload['start'],
        end=payload.get('end'),
        all_day=payload.get('all_day', False),
        location=payload.get('location', ''),
        owner=user,
        ical_uid=ical_uid or None,
        ical_sequence=ical_sequence,
        external_organizer=external_organizer or None,
        source_message=source_message,
    )
    if source:
        create_kwargs['source'] = source
    return Event.objects.create(**create_kwargs)
```

- [ ] **Step 8: Pass `source=ICS` from `ics_processor`**

In `workspace/calendar/services/ics_processor.py`, update the `create_event_from_payload(...)` call inside `_create_event` to add `source=Event.Source.ICS`. Add the `Event` import back if it was removed in Phase 2.

- [ ] **Step 9: Run all calendar tests**

Run: `uv run --no-sync python manage.py test workspace.calendar -v 2 2>&1 | tail -10`
Expected: all PASS, including the two new `Source` tests.

- [ ] **Step 10: Verify the backfill on real-ish data**

Run: `uv run python manage.py shell -c "from workspace.calendar.models import Event; print(Event.objects.values('source').annotate(c=__import__('django.db.models', fromlist=['Count']).Count('uuid')))"`
Expected: a list showing rows. In a fresh dev DB this may be empty - that is fine.

- [ ] **Step 11: Commit Phase 3**

```bash
git add workspace/calendar/models.py workspace/calendar/migrations/0015_event_source.py workspace/calendar/services/event_creation.py workspace/calendar/services/ics_processor.py workspace/calendar/tests/test_event_creation.py
git commit -m "feat(calendar): add Event.source discriminator with ICS backfill"
```

---

## Phase 4: Core LLM extraction feature (commit 4)

The biggest phase. Order matters: model first (other tasks import it), then dispatch wiring, then the worker (which uses model+dispatch), then the API and UI layer.

### Task 4.1: Add the `MailExtraction` model

**Files:**
- Modify: `workspace/mail/models.py` (append at the end, after `MailAttachment`)
- Create: `workspace/mail/migrations/0017_mailextraction.py`
- Create: `workspace/mail/tests/test_extraction_model.py`

- [ ] **Step 1: Write failing model tests**

Create `workspace/mail/tests/test_extraction_model.py`:

```python
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from workspace.calendar.models import Calendar, Event
from workspace.mail.models import (
    MailAccount, MailExtraction, MailFolder, MailMessage,
)

User = get_user_model()


class MailExtractionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='e', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='e@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            message_id='<m@x>', date=datetime.now(timezone.utc),
        )
        self.calendar = Calendar.objects.create(
            owner=self.user, name='C', color='primary',
        )
        self.event = Event.objects.create(
            calendar=self.calendar, owner=self.user, title='RDV',
            start=datetime(2026, 6, 1, 14, tzinfo=timezone.utc),
        )

    def test_extraction_round_trip(self):
        ex = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target=self.event,
            confidence='high',
            model_used='test-model',
            raw_output={'reasoning': 'concert ticket'},
        )
        ex.refresh_from_db()
        self.assertEqual(ex.target, self.event)
        self.assertEqual(ex.kind, 'event')
        self.assertEqual(ex.status, 'detected')  # default

    def test_status_dismissed(self):
        ex = MailExtraction.objects.create(
            mail_message=self.message, kind=MailExtraction.Kind.EVENT,
            target=self.event, status=MailExtraction.Status.DISMISSED,
        )
        self.assertEqual(ex.status, 'dismissed')

    def test_target_nullable_after_event_deletion(self):
        ex = MailExtraction.objects.create(
            mail_message=self.message, kind=MailExtraction.Kind.EVENT,
            target=self.event,
        )
        self.event.delete()
        ex.refresh_from_db()
        self.assertIsNone(ex.target)
        # The extraction row survives; status stays as DETECTED unless the
        # API endpoint explicitly flips it on dismiss.

    def test_cascade_when_mail_message_deleted(self):
        ex_pk = MailExtraction.objects.create(
            mail_message=self.message, kind=MailExtraction.Kind.EVENT,
            target=self.event,
        ).pk
        self.message.delete()
        self.assertFalse(MailExtraction.objects.filter(pk=ex_pk).exists())

    def test_one_message_can_have_multiple_extractions(self):
        e2 = Event.objects.create(
            calendar=self.calendar, owner=self.user, title='Train',
            start=datetime(2026, 6, 2, 9, tzinfo=timezone.utc),
        )
        MailExtraction.objects.create(
            mail_message=self.message, kind=MailExtraction.Kind.EVENT,
            target=self.event,
        )
        MailExtraction.objects.create(
            mail_message=self.message, kind=MailExtraction.Kind.EVENT,
            target=e2,
        )
        self.assertEqual(self.message.extractions.count(), 2)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_extraction_model -v 2`
Expected: tests FAIL with `ImportError: cannot import name 'MailExtraction' from 'workspace.mail.models'`.

- [ ] **Step 3: Add the model**

Append to `workspace/mail/models.py` (after the last existing model):

```python
class MailExtraction(models.Model):
    """One row per item extracted from a mail by an LLM (or by a future
    rule-based extractor). Polymorphic: `target` is a GenericForeignKey
    to whatever was extracted - today only calendar.Event, tomorrow
    possibly package trackings, invoices, etc.

    A single MailMessage can have N extractions (kind=event mail with
    two RDV creates two rows). When a target is deleted from elsewhere
    (e.g., user removes the event from the calendar UI), the FK becomes
    NULL via SET_NULL and the extraction row stays as audit.
    """

    class Kind(models.TextChoices):
        EVENT = 'event', 'Event'

    class Status(models.TextChoices):
        DETECTED = 'detected', 'Detected'
        DISMISSED = 'dismissed', 'Dismissed'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    mail_message = models.ForeignKey(
        MailMessage, on_delete=models.CASCADE, related_name='extractions',
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DETECTED,
    )

    target_content_type = models.ForeignKey(
        'contenttypes.ContentType',
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    target_object_id = models.UUIDField(null=True, blank=True)
    target = GenericForeignKey('target_content_type', 'target_object_id')

    confidence = models.CharField(max_length=8, blank=True, default='')
    model_used = models.CharField(max_length=64, blank=True, default='')
    raw_output = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['mail_message', 'kind']),
            models.Index(fields=['target_content_type', 'target_object_id']),
        ]
        ordering = ['-created_at']
```

At the top of `workspace/mail/models.py`, add the imports:

```python
from django.contrib.contenttypes.fields import GenericForeignKey
```

- [ ] **Step 4: Generate and apply the migration**

Run: `uv run python manage.py makemigrations mail`
Expected: `workspace/mail/migrations/0017_mailextraction.py` created.

Run: `uv run python manage.py migrate mail`
Expected: `Applying mail.0017_mailextraction... OK`.

- [ ] **Step 5: Run extraction tests, verify pass**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_extraction_model -v 2`
Expected: 5 tests PASS.

---

### Task 4.2: Add `AITask.TaskType.EXTRACT` and wire it into dispatch

**Files:**
- Modify: `workspace/ai/models.py:88` (the `TaskType` choices class)
- Create: `workspace/ai/migrations/00XX_aitask_extract.py` (auto-generated)
- Modify: `workspace/ai/services/dispatch.py` (add to mapping)
- Modify: `workspace/ai/tests/test_dispatch.py` (add coverage)

- [ ] **Step 1: Write failing dispatch test**

Add to `workspace/ai/tests/test_dispatch.py`:

```python
    @patch('workspace.ai.tasks.extract_from_mail_messages.delay')
    def test_extract_dispatch(self, mock_delay):
        ai_task = dispatch(
            owner=self.user,
            task_type=AITask.TaskType.EXTRACT,
            input_data={'message_uuids': ['u1']},
        )
        mock_delay.assert_called_once_with(str(ai_task.uuid))
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run --no-sync python manage.py test workspace.ai.tests.test_dispatch.DispatchTests.test_extract_dispatch -v 2`
Expected: FAIL with `AttributeError: type object 'TaskType' has no attribute 'EXTRACT'` or similar.

- [ ] **Step 3: Add the enum value**

In `workspace/ai/models.py:88`, the `class TaskType(models.TextChoices):` block, add:

```python
        EXTRACT = 'extract'
```

- [ ] **Step 4: Generate and apply the migration**

Run: `uv run python manage.py makemigrations ai && uv run python manage.py migrate ai`
Expected: a migration named like `0007_alter_aitask_task_type.py` created and applied.

- [ ] **Step 5: Add the worker import + mapping in dispatch**

In `workspace/ai/services/dispatch.py`, inside `_enqueue_worker`, update the imports and the mapping:

```python
    from workspace.ai.tasks import (
        classify_mail_messages,
        compose_email,
        editor_action,
        extract_from_mail_messages,
        summarize,
    )

    mapping = {
        AITask.TaskType.SUMMARIZE: summarize,
        AITask.TaskType.COMPOSE: compose_email,
        AITask.TaskType.REPLY: compose_email,
        AITask.TaskType.CLASSIFY: classify_mail_messages,
        AITask.TaskType.EDITOR: editor_action,
        AITask.TaskType.EXTRACT: extract_from_mail_messages,
    }
```

This import will FAIL at module load until Task 4.4 creates the worker. That is expected; the test_dispatch test still fails because of the import, then passes once the worker exists. To unblock the next steps, write a stub:

- [ ] **Step 6: Write a stub for `extract_from_mail_messages`**

Create `workspace/ai/tasks/calendar.py`:

```python
"""LLM-based extraction of calendar events from mail.

Stub at the dispatch-wiring step; the real implementation lands in
Task 4.4. The stub is enough to satisfy the import that dispatch
needs.
"""

from celery import shared_task


@shared_task(name='ai.extract_from_mail', bind=True, max_retries=0)
def extract_from_mail_messages(self, task_id: str):
    raise NotImplementedError(
        'extract_from_mail_messages is implemented in Task 4.4'
    )
```

Add the re-export in `workspace/ai/tasks/__init__.py`:

```python
from workspace.ai.tasks.calendar import extract_from_mail_messages
```

And include `'extract_from_mail_messages'` in the `__all__` list (alphabetical position).

- [ ] **Step 7: Run dispatch tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.ai.tests.test_dispatch -v 2`
Expected: all 8 tests PASS (the 7 existing plus the new one).

---

### Task 4.3: Write the LLM prompt module

**Files:**
- Create: `workspace/ai/prompts/calendar.py`
- Create: `workspace/ai/tests/test_prompts_calendar.py`

- [ ] **Step 1: Locate existing INJECTION_GUARD constant**

Run: `grep -rn "INJECTION_GUARD" workspace/ai/prompts/`
Expected: defined in `workspace/ai/prompts/mail.py` (or similar). Note the import path - the new prompt module re-uses it.

- [ ] **Step 2: Write failing prompt test**

Create `workspace/ai/tests/test_prompts_calendar.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock

from django.test import TestCase

from workspace.ai.prompts.calendar import build_event_extraction_messages


class BuildEventExtractionMessagesTests(TestCase):
    def _msg(self, subject, body, frm='alice@x.com', date='2026-05-14 09:00'):
        m = MagicMock()
        m.subject = subject
        m.body_text = body
        m.body_html = ''
        m.from_address = {'name': '', 'email': frm}
        m.date = datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc)
        return m

    def test_returns_system_and_user_messages(self):
        messages = build_event_extraction_messages([self._msg('Hi', 'Meet at 3pm tomorrow.')])
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['role'], 'user')

    def test_system_message_rejects_vague_proposals(self):
        messages = build_event_extraction_messages([self._msg('Hi', 'x')])
        system = messages[0]['content'].lower()
        self.assertIn('confirmed', system)
        self.assertIn('reject', system)

    def test_user_message_includes_thread_in_chronological_order(self):
        m1 = self._msg('Plan', 'On se voit ?')
        m2 = self._msg('Re: Plan', 'Oui mardi 14h')
        messages = build_event_extraction_messages([m1, m2])
        content = messages[1]['content']
        self.assertLess(content.index('On se voit'), content.index('Oui mardi'))

    def test_user_message_wraps_body_in_untrusted_tags(self):
        messages = build_event_extraction_messages([self._msg('Hi', 'body')])
        self.assertIn('<untrusted-content>', messages[1]['content'])
        self.assertIn('</untrusted-content>', messages[1]['content'])

    def test_long_bodies_are_truncated(self):
        long_body = 'x' * 10000
        messages = build_event_extraction_messages([self._msg('Hi', long_body)])
        # The user message should NOT contain the full 10k chars; the
        # prompt truncates at a sane per-message cap (4000 chars).
        self.assertLess(len(messages[1]['content']), 8000)
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `uv run --no-sync python manage.py test workspace.ai.tests.test_prompts_calendar -v 2`
Expected: ModuleNotFoundError.

- [ ] **Step 4: Implement the prompt**

Create `workspace/ai/prompts/calendar.py`:

```python
"""Prompt construction for LLM event extraction from mail."""

from workspace.ai.prompts.mail import INJECTION_GUARD

MAX_BODY_CHARS = 4000  # per message; threads can have many messages.

SYSTEM_PROMPT = (
    "You extract calendar events from email threads. Output rules:\n"
    "1. Return a JSON array of events. Empty array means no event.\n"
    "2. ONLY extract events that are CONFIRMED and SCHEDULED: a meeting "
    "with a date and time, a flight, a train ticket, a concert booking, "
    "a medical appointment, a restaurant reservation.\n"
    "3. REJECT vague proposals (\"we should meet sometime\"), questions "
    "(\"can we catch up next week?\"), brainstorms, marketing or "
    "promotional fluff, and anything where the user has not committed.\n"
    "4. Each event MUST have: title (string), start (ISO 8601 with "
    "timezone), end (ISO 8601 with timezone OR null), all_day (bool), "
    "location (string, may be empty), description (string, may be "
    "empty), confidence (one of 'high', 'medium', 'low'), reasoning "
    "(short free text explaining WHY you decided this is a real event).\n"
    "5. If a date is mentioned without time, set all_day=true.\n"
    "6. If the email doesn't specify a timezone, assume the recipient's "
    "local timezone (use UTC if unknown).\n"
    "7. Do NOT extract recurring events as a series; if a mail describes "
    "a recurrence, extract only the next occurrence.\n"
    "Output format: a JSON array, no other text, no markdown fences.\n"
    "Example: [{\"title\":\"Train Paris-Lyon\",\"start\":"
    "\"2026-06-01T08:00:00+02:00\",\"end\":\"2026-06-01T10:00:00+02:00\","
    "\"all_day\":false,\"location\":\"Gare de Lyon\",\"description\":\"\","
    "\"confidence\":\"high\",\"reasoning\":\"Booking confirmation with "
    "departure time and station.\"}]"
)


def build_event_extraction_messages(thread_messages: list) -> list[dict]:
    """Render a chronological thread into the chat-format messages
    expected by call_llm().

    thread_messages is a list of MailMessage instances (or compatible
    objects with subject, body_text, body_html, from_address, date),
    ordered oldest-first. Each body is truncated to MAX_BODY_CHARS.
    """
    rendered = []
    for i, m in enumerate(thread_messages, 1):
        body = (m.body_text or m.body_html or '').strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + '... [truncated]'
        from_addr = m.from_address if isinstance(m.from_address, dict) else {}
        sender = from_addr.get('email', '') or '(unknown)'
        date_str = m.date.isoformat() if m.date else ''
        rendered.append(
            f"[Message {i} | {date_str} | From: {sender} | Subject: {m.subject}]\n{body}"
        )
    thread_block = '\n\n---\n\n'.join(rendered)

    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': (
            f"Extract events from this email thread:\n\n"
            f"<untrusted-content>\n{thread_block}\n</untrusted-content>\n\n"
            f"{INJECTION_GUARD}"
        )},
    ]
```

- [ ] **Step 5: Run prompt tests, verify pass**

Run: `uv run --no-sync python manage.py test workspace.ai.tests.test_prompts_calendar -v 2`
Expected: 5 tests PASS.

---

### Task 4.4: Implement the `extract_from_mail_messages` worker

**Files:**
- Modify: `workspace/ai/tasks/calendar.py` (replace the stub from Task 4.2)
- Create: `workspace/ai/tests/test_extract_from_mail.py`

- [ ] **Step 1: Write failing worker tests**

Create `workspace/ai/tests/test_extract_from_mail.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_tz

from workspace.ai.models import AITask
from workspace.calendar.models import Event
from workspace.mail.models import (
    MailAccount, MailExtraction, MailFolder, MailMessage,
)

User = get_user_model()


def _llm_payload(events):
    """Mirror call_llm()'s return shape, with events as a JSON string."""
    import json
    return {
        'content': json.dumps(events),
        'tool_calls': [],
        'model': 'test-model',
        'prompt_tokens': 100,
        'completion_tokens': 20,
    }


class ExtractFromMailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ex', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='ex@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            message_id='<m@x>', date=dj_tz.now(),
            subject='Train', body_text='Confirmed for tomorrow 8am.',
            from_address={'email': 'sncf@x.com'},
        )
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.EXTRACT,
            input_data={'message_uuids': [str(self.message.uuid)]},
        )

    @patch('workspace.ai.tasks.calendar.call_llm')
    def test_creates_event_and_extraction_on_high_confidence(self, mock_llm):
        future = (dj_tz.now() + timedelta(days=1)).isoformat()
        mock_llm.return_value = _llm_payload([{
            'title': 'Train Paris-Lyon',
            'start': future,
            'end': None, 'all_day': False, 'location': 'Gare de Lyon',
            'description': '', 'confidence': 'high', 'reasoning': 'ticket'
        }])
        from workspace.ai.tasks.calendar import extract_from_mail_messages
        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 1)
        ex = MailExtraction.objects.first()
        self.assertEqual(ex.kind, 'event')
        self.assertEqual(ex.confidence, 'high')
        self.assertIsNotNone(ex.target)
        self.assertEqual(ex.target.source, 'llm')

    @patch('workspace.ai.tasks.calendar.call_llm')
    def test_drops_medium_confidence(self, mock_llm):
        future = (dj_tz.now() + timedelta(days=1)).isoformat()
        mock_llm.return_value = _llm_payload([{
            'title': 'Maybe coffee', 'start': future, 'end': None,
            'all_day': False, 'location': '', 'description': '',
            'confidence': 'medium', 'reasoning': 'vague',
        }])
        from workspace.ai.tasks.calendar import extract_from_mail_messages
        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)

    @patch('workspace.ai.tasks.calendar.call_llm')
    def test_drops_past_events(self, mock_llm):
        past = (dj_tz.now() - timedelta(days=1)).isoformat()
        mock_llm.return_value = _llm_payload([{
            'title': 'Yesterday RDV', 'start': past, 'end': None,
            'all_day': False, 'location': '', 'description': '',
            'confidence': 'high', 'reasoning': 'old',
        }])
        from workspace.ai.tasks.calendar import extract_from_mail_messages
        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    @patch('workspace.ai.tasks.calendar.call_llm')
    def test_empty_array_creates_nothing(self, mock_llm):
        mock_llm.return_value = _llm_payload([])
        from workspace.ai.tasks.calendar import extract_from_mail_messages
        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)

    @patch('workspace.ai.tasks.calendar.call_llm')
    def test_multi_event_creates_multiple_rows(self, mock_llm):
        f1 = (dj_tz.now() + timedelta(days=1)).isoformat()
        f2 = (dj_tz.now() + timedelta(days=2)).isoformat()
        mock_llm.return_value = _llm_payload([
            {'title': 'A', 'start': f1, 'end': None, 'all_day': False,
             'location': '', 'description': '', 'confidence': 'high',
             'reasoning': 'r1'},
            {'title': 'B', 'start': f2, 'end': None, 'all_day': False,
             'location': '', 'description': '', 'confidence': 'high',
             'reasoning': 'r2'},
        ])
        from workspace.ai.tasks.calendar import extract_from_mail_messages
        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.filter(mail_message=self.message).count(), 2)

    @patch('workspace.ai.tasks.calendar.call_llm')
    def test_malformed_json_skips_message_without_crashing(self, mock_llm):
        mock_llm.return_value = {
            'content': 'not json at all',
            'tool_calls': [], 'model': 'test', 'prompt_tokens': 1, 'completion_tokens': 1,
        }
        from workspace.ai.tasks.calendar import extract_from_mail_messages
        extract_from_mail_messages(str(self.ai_task.uuid))

        self.assertEqual(MailExtraction.objects.count(), 0)
        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.status, AITask.Status.COMPLETED)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run --no-sync python manage.py test workspace.ai.tests.test_extract_from_mail -v 2`
Expected: all 6 tests FAIL (NotImplementedError from the stub, or import errors).

- [ ] **Step 3: Implement the worker**

Replace the content of `workspace/ai/tasks/calendar.py` with:

```python
"""LLM-based extraction of calendar events from mail.

For each new mail synced in a batch, reconstruct the thread, call the
LLM with a strict event-extraction prompt, validate the response, and
materialize an Event + MailExtraction for each high-confidence,
future-dated entry. Per-message transactions: one bad mail does not
poison the batch.
"""

import logging
import re
from datetime import datetime
from typing import Literal

import orjson
from celery import shared_task
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone as dj_tz
from pydantic import BaseModel, ValidationError

from workspace.ai.models import AITask
from workspace.ai.prompts.calendar import build_event_extraction_messages
from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import call_llm
from workspace.calendar.models import Event
from workspace.calendar.services.event_creation import create_event_from_payload
from workspace.common.logging import scrub
from workspace.mail.models import MailExtraction, MailMessage
from workspace.mail.services.threads import get_thread

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$', re.MULTILINE)


class ExtractedEvent(BaseModel):
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    location: str = ''
    description: str = ''
    confidence: Literal['high', 'medium', 'low']
    reasoning: str = ''


@shared_task(name='ai.extract_from_mail', bind=True, max_retries=0)
def extract_from_mail_messages(self, task_id: str):
    """Run LLM event extraction over the messages referenced by `task_id`.

    Errors on a per-message basis are logged and skipped: the task as a
    whole still reaches COMPLETED unless ai_task_lifecycle catches a
    fatal exception.
    """
    try:
        with ai_task_lifecycle(task_id, log_label='Extract') as ai_task:
            message_uuids = ai_task.input_data.get('message_uuids', [])
            msgs = list(
                MailMessage.objects.filter(
                    uuid__in=message_uuids,
                    account__owner=ai_task.owner,
                ).select_related('account')
            )

            if not msgs:
                ai_task.result = 'No messages to extract from'
                return {'status': 'ok', 'task_id': task_id}

            total_prompt = 0
            total_completion = 0
            model_used = ''
            event_ct = ContentType.objects.get_for_model(Event)
            extractions_created = 0

            for msg in msgs:
                try:
                    created = _extract_one_message(msg, event_ct)
                    extractions_created += created['count']
                    total_prompt += created['prompt_tokens']
                    total_completion += created['completion_tokens']
                    if created['model']:
                        model_used = created['model']
                except Exception:
                    logger.exception(
                        'Extract: failed for message %s', scrub(str(msg.pk))
                    )

            ai_task.result = f'Created {extractions_created} extractions from {len(msgs)} messages'
            ai_task.model_used = model_used
            ai_task.prompt_tokens = total_prompt
            ai_task.completion_tokens = total_completion

            return {'status': 'ok', 'task_id': task_id}
    except AITask.DoesNotExist:
        logger.warning('Extract: AITask %s not found', scrub(task_id))
        return {'status': 'error', 'task_id': task_id}


def _extract_one_message(msg: MailMessage, event_ct: ContentType) -> dict:
    thread = get_thread(msg)
    messages = build_event_extraction_messages(thread)
    result = call_llm(messages, model=settings.AI_MODEL)

    raw_content = _FENCE_RE.sub('', (result.get('content') or '').strip())
    try:
        items = orjson.loads(raw_content)
    except (ValueError, TypeError):
        logger.warning('Extract: malformed JSON for message %s', scrub(str(msg.pk)))
        return {
            'count': 0,
            'prompt_tokens': result.get('prompt_tokens', 0) or 0,
            'completion_tokens': result.get('completion_tokens', 0) or 0,
            'model': result.get('model', ''),
        }
    if not isinstance(items, list):
        logger.warning('Extract: expected JSON array, got %s', type(items).__name__)
        return {
            'count': 0,
            'prompt_tokens': result.get('prompt_tokens', 0) or 0,
            'completion_tokens': result.get('completion_tokens', 0) or 0,
            'model': result.get('model', ''),
        }

    created = 0
    now = dj_tz.now()
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        try:
            extracted = ExtractedEvent.model_validate(raw_item)
        except ValidationError:
            logger.debug('Extract: invalid event shape from LLM, dropping')
            continue

        if extracted.confidence != 'high':
            logger.debug('Extract: dropping non-high confidence event')
            continue
        if extracted.start <= now:
            logger.debug('Extract: dropping past-dated event')
            continue

        with transaction.atomic():
            event = create_event_from_payload(
                user=msg.account.owner,
                payload={
                    'title': extracted.title,
                    'start': extracted.start,
                    'end': extracted.end,
                    'all_day': extracted.all_day,
                    'location': extracted.location,
                    'description': extracted.description,
                },
                source=Event.Source.LLM,
                source_message=msg,
            )
            MailExtraction.objects.create(
                mail_message=msg,
                kind=MailExtraction.Kind.EVENT,
                target_content_type=event_ct,
                target_object_id=event.uuid,
                confidence=extracted.confidence,
                model_used=result.get('model', ''),
                raw_output=raw_item,
            )
        created += 1

    return {
        'count': created,
        'prompt_tokens': result.get('prompt_tokens', 0) or 0,
        'completion_tokens': result.get('completion_tokens', 0) or 0,
        'model': result.get('model', ''),
    }
```

- [ ] **Step 4: Run worker tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.ai.tests.test_extract_from_mail -v 2`
Expected: 6 tests PASS.

---

### Task 4.5: Hook the dispatch in `imap_sync.py`

**Files:**
- Modify: `workspace/mail/services/imap_sync.py:189-210` (right after the classify dispatch)
- Modify: `workspace/mail/tests/test_extract_sync_hook.py` (new file)

- [ ] **Step 1: Write a failing sync-hook test**

Create `workspace/mail/tests/test_extract_sync_hook.py` modeled after `workspace/mail/tests/test_ics_sync_hook.py`:

```python
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class SyncExtractDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='s', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='s@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )

    @patch('workspace.ai.services.dispatch.dispatch')
    @patch('workspace.users.services.settings.get_setting', return_value=True)
    @patch('workspace.ai.client.is_ai_enabled', return_value=True)
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    @patch('workspace.mail.services.imap_sync._reconcile_folder')
    @patch('workspace.mail.services.imap_sync._parse_message')
    @patch('workspace.mail.services.imap_sync.connect_imap')
    def test_extract_dispatched_for_new_messages(
        self, mock_conn, mock_parse, _mock_recon, _mock_proc,
        _mock_ai, _mock_setting, mock_dispatch,
    ):
        from workspace.mail.services.imap_sync import sync_folder_messages

        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<n@x>', imap_uid=2, subject='S', date='2026-05-14T10:00:00Z',
        )
        mock_parse.return_value = new_msg

        conn = MagicMock()
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])
        mock_conn.return_value = conn

        sync_folder_messages(self.account, self.folder)

        # Two dispatches: one CLASSIFY, one EXTRACT, both with the new message UUID.
        task_types = [c.kwargs.get('task_type') for c in mock_dispatch.call_args_list]
        from workspace.ai.models import AITask
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertIn(AITask.TaskType.EXTRACT, task_types)

    @patch('workspace.ai.services.dispatch.dispatch')
    @patch('workspace.users.services.settings.get_setting', return_value=False)  # disabled
    @patch('workspace.ai.client.is_ai_enabled', return_value=True)
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    @patch('workspace.mail.services.imap_sync._reconcile_folder')
    @patch('workspace.mail.services.imap_sync._parse_message')
    @patch('workspace.mail.services.imap_sync.connect_imap')
    def test_extract_skipped_when_user_disabled_ai(
        self, mock_conn, mock_parse, _mock_recon, _mock_proc,
        _mock_ai, _mock_setting, mock_dispatch,
    ):
        from workspace.mail.services.imap_sync import sync_folder_messages

        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<n@x>', imap_uid=2, subject='S', date='2026-05-14T10:00:00Z',
        )
        mock_parse.return_value = new_msg

        conn = MagicMock()
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])
        mock_conn.return_value = conn

        sync_folder_messages(self.account, self.folder)

        mock_dispatch.assert_not_called()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_extract_sync_hook -v 2`
Expected: at least one test FAILS (EXTRACT dispatch not yet wired).

- [ ] **Step 3: Wire the extract dispatch**

In `workspace/mail/services/imap_sync.py`, locate the classify dispatch block (~lines 189-206). Add an extract dispatch directly after the classify dispatch, inside the same `if is_ai_enabled() and get_setting(...)` block:

```python
                if is_ai_enabled() and get_setting(account.owner, 'mail', 'ai_enabled', default=True):
                    from workspace.ai.models import AITask
                    from workspace.ai.services.dispatch import dispatch
                    dispatch(
                        owner=account.owner,
                        task_type=AITask.TaskType.CLASSIFY,
                        input_data={'message_uuids': new_message_uuids},
                    )
                    dispatch(
                        owner=account.owner,
                        task_type=AITask.TaskType.EXTRACT,
                        input_data={'message_uuids': new_message_uuids},
                    )
                    logger.info(
                        'Dispatched classify+extract tasks for %d new messages in %s',
                        len(new_message_uuids), scrub(folder.name),
                    )
```

- [ ] **Step 4: Run hook tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_extract_sync_hook -v 2`
Expected: 2 tests PASS.

---

### Task 4.6: Serializer and detail endpoint

**Files:**
- Modify: `workspace/mail/serializers.py` (add `MailExtractionSerializer`, add `extractions` field)
- Modify: `workspace/mail/tests/test_serializers.py` (or wherever detail serializer tests live)

- [ ] **Step 1: Find the detail serializer**

Run: `grep -n "MailMessageDetailSerializer\|class Mail.*Serializer" workspace/mail/serializers.py | head -10`
Expected: a list of serializer class declarations. Note where `MailMessageDetailSerializer` is defined.

- [ ] **Step 2: Write a failing test**

Add to the test file (likely `workspace/mail/tests/test_serializers.py`; create one if absent following the project pattern):

```python
def test_detail_includes_extractions(self):
    """MailMessageDetailSerializer must include the related extractions."""
    from workspace.calendar.models import Calendar, Event
    from workspace.mail.models import MailExtraction
    from workspace.mail.serializers import MailMessageDetailSerializer
    from django.contrib.contenttypes.models import ContentType

    cal = Calendar.objects.create(owner=self.user, name='C', color='primary')
    ev = Event.objects.create(
        calendar=cal, owner=self.user, title='X',
        start=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
    )
    MailExtraction.objects.create(
        mail_message=self.message,
        kind=MailExtraction.Kind.EVENT,
        target_content_type=ContentType.objects.get_for_model(Event),
        target_object_id=ev.uuid,
    )

    data = MailMessageDetailSerializer(self.message).data
    self.assertEqual(len(data['extractions']), 1)
    self.assertEqual(data['extractions'][0]['kind'], 'event')
    self.assertEqual(data['extractions'][0]['target']['title'], 'X')

def test_dismissed_extractions_excluded_from_detail(self):
    from workspace.calendar.models import Calendar, Event
    from workspace.mail.models import MailExtraction
    from workspace.mail.serializers import MailMessageDetailSerializer
    from django.contrib.contenttypes.models import ContentType

    cal = Calendar.objects.create(owner=self.user, name='C', color='primary')
    ev = Event.objects.create(
        calendar=cal, owner=self.user, title='X',
        start=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
    )
    MailExtraction.objects.create(
        mail_message=self.message,
        kind=MailExtraction.Kind.EVENT,
        status=MailExtraction.Status.DISMISSED,
        target_content_type=ContentType.objects.get_for_model(Event),
        target_object_id=ev.uuid,
    )
    data = MailMessageDetailSerializer(self.message).data
    self.assertEqual(data['extractions'], [])
```

- [ ] **Step 3: Run test, verify it fails**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_serializers -v 2`
Expected: KeyError on `'extractions'` or similar.

- [ ] **Step 4: Implement the serializer**

In `workspace/mail/serializers.py`, before `MailMessageDetailSerializer`:

```python
class _ExtractionTargetEventSerializer(serializers.Serializer):
    """Minimal embed of a calendar.Event inside a MailExtraction payload.
    Defined here to avoid a circular import from calendar.serializers."""
    uuid = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    start = serializers.DateTimeField(read_only=True)
    end = serializers.DateTimeField(read_only=True, allow_null=True)
    all_day = serializers.BooleanField(read_only=True)
    location = serializers.CharField(read_only=True)


class MailExtractionSerializer(serializers.ModelSerializer):
    target = serializers.SerializerMethodField()

    class Meta:
        model = MailExtraction
        fields = ['uuid', 'kind', 'target']

    def get_target(self, obj):
        target = obj.target
        if target is None:
            return None
        if obj.kind == MailExtraction.Kind.EVENT:
            return _ExtractionTargetEventSerializer(target).data
        return None
```

In `MailMessageDetailSerializer`, add the field. The source filters out DISMISSED rows:

```python
class MailMessageDetailSerializer(serializers.ModelSerializer):
    ...
    extractions = serializers.SerializerMethodField()

    class Meta:
        model = MailMessage
        fields = [..., 'extractions']

    def get_extractions(self, obj):
        qs = obj.extractions.filter(
            status=MailExtraction.Status.DETECTED,
        ).select_related('target_content_type')
        return MailExtractionSerializer(qs, many=True).data
```

Add the import at the top:

```python
from workspace.mail.models import MailExtraction
```

(Keep the relative position consistent with existing imports.)

- [ ] **Step 5: Run tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_serializers -v 2`
Expected: both new tests PASS.

---

### Task 4.7: `DELETE /api/v1/mail/extractions/<uuid>` endpoint

**Files:**
- Create: `workspace/mail/views_extractions.py`
- Modify: `workspace/mail/urls.py`
- Create: `workspace/mail/tests/test_api_extractions.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `workspace/mail/tests/test_api_extractions.py`:

```python
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event
from workspace.mail.models import (
    MailAccount, MailExtraction, MailFolder, MailMessage,
)

User = get_user_model()


class ExtractionsDeleteTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='p')
        self.client.force_authenticate(self.user)
        self.account = MailAccount.objects.create(
            owner=self.user, email='d@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )
        self.message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            message_id='<m@x>', date=datetime.now(timezone.utc),
        )
        self.calendar = Calendar.objects.create(owner=self.user, name='C', color='primary')
        self.event = Event.objects.create(
            calendar=self.calendar, owner=self.user, title='X',
            start=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        )
        self.ex = MailExtraction.objects.create(
            mail_message=self.message,
            kind=MailExtraction.Kind.EVENT,
            target_content_type=ContentType.objects.get_for_model(Event),
            target_object_id=self.event.uuid,
        )

    def test_delete_flips_status_and_removes_event(self):
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.ex.refresh_from_db()
        self.assertEqual(self.ex.status, 'dismissed')
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_delete_is_idempotent(self):
        self.ex.status = MailExtraction.Status.DISMISSED
        self.ex.save()
        self.event.delete()
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_other_users_extraction_returns_404(self):
        other = User.objects.create_user(username='o', password='p')
        self.client.force_authenticate(other)
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_returns_401_or_403(self):
        self.client.force_authenticate(None)
        url = f'/api/v1/mail/extractions/{self.ex.uuid}'
        resp = self.client.delete(url)
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
```

- [ ] **Step 2: Run tests, verify they fail (404 on missing route)**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_api_extractions -v 2`
Expected: FAIL with route-not-found.

- [ ] **Step 3: Implement the view**

Create `workspace/mail/views_extractions.py`:

```python
import logging

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none
from workspace.mail.models import MailExtraction

logger = logging.getLogger(__name__)


class ExtractionDetailView(APIView):
    """DELETE /api/v1/mail/extractions/<uuid> - dismiss an extraction.

    Sets status=DISMISSED and, for kind=event, deletes the produced Event
    in the same transaction. Idempotent: already-dismissed rows return 204.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, extraction_id):
        ex_uuid = parse_uuid_or_none(str(extraction_id))
        if ex_uuid is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            ex = MailExtraction.objects.select_related('mail_message__account').get(
                uuid=ex_uuid,
                mail_message__account__owner=request.user,
            )
        except MailExtraction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            if ex.status != MailExtraction.Status.DISMISSED:
                ex.status = MailExtraction.Status.DISMISSED
                ex.save(update_fields=['status'])

            target = ex.target
            if ex.kind == MailExtraction.Kind.EVENT and target is not None:
                target.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Add the route**

In `workspace/mail/urls.py`, add the import and the path. The path is `extractions/<uuid:extraction_id>` (no trailing slash per the API rule):

```python
from workspace.mail.views_extractions import ExtractionDetailView

urlpatterns = [
    ...
    path('extractions/<uuid:extraction_id>', ExtractionDetailView.as_view()),
]
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `uv run --no-sync python manage.py test workspace.mail.tests.test_api_extractions -v 2`
Expected: 4 tests PASS.

---

### Task 4.8: UI - widget in `message_detail.html` + dismiss handler

**Files:**
- Modify: `workspace/mail/ui/templates/mail/ui/partials/message_detail.html` (~ line 213, after AI summary)
- Modify: `workspace/mail/ui/static/mail/ui/js/mail_messages.js` (add `dismissExtraction()`)

- [ ] **Step 1: Locate the injection point in the template**

Run: `grep -n "ai_summary_html\|ai_summary\b" workspace/mail/ui/templates/mail/ui/partials/message_detail.html | head -5`
Expected: the line range around 197-213 where AI summary is rendered. Confirm visually.

- [ ] **Step 2: Add the widget**

Inside `workspace/mail/ui/templates/mail/ui/partials/message_detail.html`, immediately AFTER the AI summary section and BEFORE the body content section, add:

```django
<template x-for="extraction in (messageDetail.extractions || [])" :key="extraction.uuid">
  <div x-show="extraction.kind === 'event' && extraction.target"
       x-cloak
       class="mb-5 rounded-lg border border-accent/15 bg-gradient-to-br from-accent/5 via-transparent to-accent/3 overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2 border-b border-accent/10 bg-accent/5">
      <i data-lucide="calendar-plus" class="w-3.5 h-3.5 text-accent flex-shrink-0"></i>
      <span class="text-xs font-semibold text-accent tracking-wide uppercase flex-1">Suggested event</span>
    </div>
    <div class="px-3 py-2.5 text-sm space-y-2">
      <div class="font-medium" x-text="extraction.target.title"></div>
      <div class="text-base-content/70 text-xs" x-text="extraction.target.start"></div>
      <div x-show="extraction.target.location" class="text-base-content/70 text-xs" x-text="extraction.target.location"></div>
      <div class="flex items-center gap-2 pt-1">
        <a :href="`/calendar?event=${extraction.target.uuid}`" class="btn btn-xs btn-ghost">
          View in calendar
        </a>
        <button type="button"
                class="btn btn-xs btn-ghost text-error/80"
                @click="dismissExtraction(extraction, messageDetail)">
          Remove
        </button>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Implement `dismissExtraction` in the JS**

In `workspace/mail/ui/static/mail/ui/js/mail_messages.js`, add a method to the existing Alpine component (likely returned by a factory function near the top of the file). Locate the method that handles other per-message actions (e.g., `toggleStar`, `summarizeMessage`) and add `dismissExtraction` alongside:

```javascript
async dismissExtraction(extraction, message) {
  // Optimistic: drop locally first; refresh on failure handled by reload-on-error pattern.
  if (message && Array.isArray(message.extractions)) {
    message.extractions = message.extractions.filter(e => e.uuid !== extraction.uuid);
  }
  try {
    const resp = await fetch(`/api/v1/mail/extractions/${extraction.uuid}`, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': getCSRFToken() },
    });
    if (!resp.ok && resp.status !== 404) {
      throw new Error(`HTTP ${resp.status}`);
    }
  } catch (e) {
    // Re-fetch the message to restore truth from the server.
    console.error('Failed to dismiss extraction:', e);
    if (typeof this.refreshMessage === 'function') {
      this.refreshMessage(message);
    }
  }
},
```

If `getCSRFToken` is not already imported/defined in the file, copy the pattern from another Alpine component in the same module (e.g., `toggleRead` typically does a CSRF-bearing fetch).

- [ ] **Step 4: Manual UI verification**

Start the dev server: `uv run python manage.py runserver`
Steps:
1. Open the mail UI in a browser.
2. (Optional) Trigger an extraction manually by running the worker from a shell: `uv run python manage.py shell -c "from workspace.ai.tasks.calendar import extract_from_mail_messages; extract_from_mail_messages('<an-extract-AITask-uuid>')"`
3. Confirm the widget appears, the "View in calendar" link points to a valid URL, and "Remove" makes the widget disappear and removes the event from the calendar.

If the dev environment does not have AI configured (the common case), at minimum confirm that:
- The template renders without errors when `extractions: []` (empty case - widget not visible, no console errors).
- A fixture-injected extraction shows the widget correctly (use the Django shell to create one for testing).

- [ ] **Step 5: Run the full mail + ai + calendar test suites**

Run in sequence:
```
uv run --no-sync python manage.py test workspace.mail
uv run --no-sync python manage.py test workspace.ai
uv run --no-sync python manage.py test workspace.calendar
```
Expected: all green.

- [ ] **Step 6: Commit Phase 4**

```bash
git add workspace/mail/models.py \
        workspace/mail/migrations/0017_mailextraction.py \
        workspace/mail/serializers.py \
        workspace/mail/views_extractions.py \
        workspace/mail/urls.py \
        workspace/mail/services/imap_sync.py \
        workspace/mail/tests/test_extraction_model.py \
        workspace/mail/tests/test_serializers.py \
        workspace/mail/tests/test_extract_sync_hook.py \
        workspace/mail/tests/test_api_extractions.py \
        workspace/mail/ui/templates/mail/ui/partials/message_detail.html \
        workspace/mail/ui/static/mail/ui/js/mail_messages.js \
        workspace/ai/models.py \
        workspace/ai/migrations \
        workspace/ai/prompts/calendar.py \
        workspace/ai/services/dispatch.py \
        workspace/ai/tasks/__init__.py \
        workspace/ai/tasks/calendar.py \
        workspace/ai/tests/test_dispatch.py \
        workspace/ai/tests/test_prompts_calendar.py \
        workspace/ai/tests/test_extract_from_mail.py
git commit -m "feat(ai): extract calendar events from mail with LLM"
```

(Glob `workspace/ai/migrations` picks up the new alter-task-type migration generated in Task 4.2.)

---

## Phase wrap-up

### Final integration check

- [ ] **Step 1: Full test run across affected modules**

```bash
uv run --no-sync python manage.py test workspace.mail workspace.ai workspace.calendar workspace.notifications 2>&1 | tail -10
```
Expected: all green. `notifications` included because event creation can fire a notify().

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/llm-event-extraction-from-mail
gh pr create --title "feat(ai): extract calendar events from mail with LLM" --body "$(cat <<'EOF'
## Summary

- Adds an LLM-driven extraction pass on incoming mail. When a new message is synced, an event-extraction worker reconstructs the thread, asks the model to spot any confirmed, scheduled event (RDV, train ticket, concert booking, ...), and creates a calendar Event linked back to the mail.
- The UI surfaces the suggested event in the mail detail with the same visual shape as ICS invitations and a Remove button that dismisses it (deletes the underlying Event).
- Designed to be extended: the new MailExtraction model is polymorphic (kind=event today, kind=package/invoice/... tomorrow) and the Event.source discriminator distinguishes ICS-from-invitations, LLM-extracted, and manual events.

## Spec

`docs/superpowers/specs/2026-05-14-llm-event-extraction-from-mail-design.md` (committed in this PR).

## Test plan

- [x] `uv run python manage.py test workspace.mail`
- [x] `uv run python manage.py test workspace.ai`
- [x] `uv run python manage.py test workspace.calendar`
- [ ] Manual: trigger an extraction in dev (shell), confirm widget appears, "Remove" deletes the event.
EOF
)"
```

---

## Self-review notes

- **Spec coverage check** (cross-referencing `docs/superpowers/specs/2026-05-14-llm-event-extraction-from-mail-design.md`):
  - 3.1 Trigger - Task 4.5
  - 3.2 Worker - Task 4.4
  - 3.3 LLM extraction (prompt, schema, validation) - Tasks 4.3, 4.4
  - 3.4 Thread reconstruction - Tasks 1.1, 1.2, 1.3
  - 3.5 MailExtraction - Task 4.1
  - 3.6 Event.source - Task 3.1
  - 3.7 Shared event creation - Tasks 2.2, 2.3
  - 3.8 UI - Task 4.8
  - 3.9 API endpoints - Tasks 4.6 (GET extension), 4.7 (DELETE)
  - 3.10 Settings - unchanged; reuses `mail.ai_enabled` (Task 4.5)
  - 5 Migrations - covered by Tasks 1.1, 3.1, 4.1, 4.2 (auto-generated where applicable)

- **Type consistency:** `MailExtraction.Kind.EVENT` and `MailExtraction.Status.DETECTED/DISMISSED` are used consistently in tests and code. `Event.Source.LLM/ICS/MANUAL` ditto. The worker uses `ExtractedEvent` Pydantic model, never `dict`.

- **No placeholders:** every step has concrete code or commands. No "implement appropriate error handling" or "add validation" without showing how. Each TDD step has both test code and verification commands.
