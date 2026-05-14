# LLM event extraction from mail - design

Status: draft - awaiting user review
Date: 2026-05-14
Branch: `feat/llm-event-extraction-from-mail`

## 1. Goal

When a new email arrives, run an LLM pass over its content (and thread context) to detect any concrete, scheduled event (a meeting, a train ticket, a concert booking, an appointment), create a corresponding `Event` row in the user's calendar, and surface it in the mail-reading UI next to the message - mirroring the way an ICS attachment is already surfaced today.

Out of scope: extracting "soft" events (proposals, brainstorms, questions like "shall we meet next week?"). The feature is strict: if it's not unambiguously confirmed with a date/time, it's dropped.

## 2. Non-goals

- Backfilling already-synced emails. The classify and summarize features do not backfill, and adding backfill here would be a per-user N-mail LLM cost spike. Only emails freshly synced after the feature ships get analyzed. A future manual "re-extract" button can be added without schema change.
- A "maybe this is an event?" UI affordance. Confidence is binary in the user experience: either we created an event, or we didn't.
- Calendar editing UX changes. Once an `Event` exists, it lives in the calendar like any other event - the existing UI handles edits, deletions, time changes.
- Modeling the *thread* as a first-class entity. The feature reads upstream messages by walking In-Reply-To, but does not introduce a `MailThread` table.

## 3. Design

### 3.1 Trigger

Identical placement to the existing classify trigger in `workspace/mail/services/imap_sync.py` (~line 189). After a folder sync, if:

- AI is globally enabled (`workspace.ai.client.is_ai_enabled()` returns True)
- The user setting `mail.ai_enabled` is true (default true, read via `workspace.users.services.settings.get_setting`)
- The folder is not `sent` or `drafts`
- `new_message_uuids` (UUIDs of messages just inserted this sync) is non-empty

then dispatch a new `AITask.TaskType.EXTRACT` via the recently-introduced `workspace.ai.services.dispatch.dispatch(...)`. The classify dispatch already lives in this exact block; the extract dispatch sits right next to it, gated by the same conditions (no duplicate gating code).

### 3.2 Worker

A new Celery task `workspace.ai.tasks.calendar.extract_from_mail_messages`, registered in `workspace/ai/tasks/__init__.py`, and added to the `_enqueue_worker` mapping in `workspace.ai.services.dispatch`. The existing `test_every_task_type_has_a_worker` test catches the mapping omission.

The worker:

1. Loads `MailMessage` rows by UUID under `account__owner=ai_task.owner`.
2. Groups by account (LLM cost tracking & future per-account model selection).
3. **For each message**, calls the LLM **once** (no batching - see "Why per-message" below) with the full thread reconstructed in chronological order.
4. Parses the JSON array response, validates each entry through `ExtractedEvent` (Pydantic), drops anything that isn't `confidence='high'` or whose `start` is in the past.
5. For each surviving entry, creates an `Event` (`source=Source.LLM`, `source_message=mail_message`) and a `MailExtraction(kind=EVENT, target=<event>)`. Both writes happen inside one `transaction.atomic` scoped to that single message - so a parse error or DB failure on message N leaves messages 1..N-1 committed and 1..N untouched, and the worker continues to message N+1. The whole task does NOT use a single outer transaction; per-message isolation is intentional so one bad mail doesn't poison the batch.
6. Lifecycle managed by `ai_task_lifecycle` exactly like classify/summarize (PENDING -> PROCESSING -> COMPLETED/FAILED + SSE notify). The lifecycle status reflects the worker reaching the end, not per-mail success rate (which is logged but not surfaced).

Why per-message: each mail's prompt is its own thread (variable length, can reach the context window cap on long threads). Batching 10 threads would force truncation. The fixed cost of 10 separate LLM calls vs 1 batched call is justified by the per-thread context window need.

### 3.3 LLM extraction

**Prompt** (`workspace/ai/prompts/calendar.py`, new file):

```python
def build_event_extraction_messages(thread_messages: list[MailMessage]) -> list[dict]:
    """System: 'Extract calendar events from the email thread below.
    Only extract events that are confirmed, scheduled, and have a real
    date and time. Reject vague proposals ("we should meet sometime"),
    questions ("can we meet next week?"), brainstorms, marketing fluff.
    Return a JSON array (may be empty). Wrap output in <untrusted> guard.'
    
    User: thread rendered chronologically:
        ---
        [Mail 1, 2026-05-14 09:12] From: alice@x.com | Subject: ...
        <body, truncated to 4000 chars per message>
        ---
        [Mail 2, ...] ...
    """
```

**Output schema** (Pydantic):

```python
class ExtractedEvent(BaseModel):
    title: str
    start: datetime  # ISO 8601 with tz, parsed via pydantic
    end: datetime | None = None
    all_day: bool = False
    location: str = ''
    description: str = ''
    confidence: Literal['high', 'medium', 'low']
    reasoning: str  # free text, kept in MailExtraction.raw_output for audit, never shown
```

**Validation gate**: `confidence == 'high'` AND `start > now`. Anything else is dropped (with a debug log including `reasoning`).

**Model**: `settings.AI_MODEL` (the larger model). The classify uses `AI_SMALL_MODEL` because tag assignment is shallow; event extraction needs the model to reason about whether a phrase like "let's catch up Tuesday" is a real plan or a polite filler.

**Injection guard**: identical to classify - wrap mail content in `<untrusted-content>...</untrusted-content>` plus the existing `INJECTION_GUARD` constant. JSON parsing is fence-tolerant (the existing `_FENCE_RE` regex is reused).

### 3.4 Thread reconstruction

Today `MailMessage` carries no thread metadata - no `in_reply_to`, no `references`, no `thread_id`. Adding minimal support is part of this feature:

**Schema change**:
```python
class MailMessage(models.Model):
    ...
    in_reply_to = models.CharField(max_length=512, blank=True, default='')
```

That's the single field needed; references-chain walking is overkill (most threads are shallow, and IMAP servers occasionally drop the References header on forwards). One field + the existing `message_id` are enough to reconstruct a parent->child chain.

**Parser change** (`workspace/mail/services/imap_parse.py`): read the `In-Reply-To` header during message parsing and store it on the row. No backfill - old rows keep `in_reply_to=''` and behave as standalone messages in the extraction prompt.

**Reconstruction helper** (`workspace/mail/services/threads.py`, new):
```python
def get_thread(message: MailMessage, max_depth: int = 20) -> list[MailMessage]:
    """Walk in_reply_to upward, returning [ancestor, ..., parent, message]
    in chronological order. The walk: starting from `message`, look up
    a MailMessage in the same account with message_id == message.in_reply_to,
    repeat until in_reply_to is empty or no parent matches.
    
    Capped at max_depth to bound LLM cost on pathological reply chains.
    Returns [message] alone if no chain found (no in_reply_to, broken
    chain, or a parent that's not in our DB - e.g., a thread where we
    only have the latest reply)."""
```

The cap matters: a 200-message thread should not silently quadruple the LLM bill. Past `max_depth`, oldest messages are simply dropped from the prompt - the LLM still has recent context.

**Limitation acknowledged**: some IMAP servers strip `In-Reply-To` on forwards or don't set it on the very first reply (instead populating only `References`). For v1 we accept that those threads will be processed as single messages. If real-world precision suffers, a follow-up can parse the last entry of `References` as a fallback parent lookup.

### 3.5 Data model - `MailExtraction`

New model in `workspace/mail/models.py` (semantically belongs to "things known about a mail"):

```python
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class MailExtraction(models.Model):
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
        ContentType, on_delete=models.SET_NULL, null=True, blank=True,
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
```

**1-N from `MailMessage` to `MailExtraction`**: one mail can produce multiple extractions (a mail mentioning two events spawns two rows; tomorrow, a mail mentioning one event plus one package tracking number spawns two rows of different `kind`). No unique constraint on `(mail_message, kind)`.

**`Status.DISMISSED`**: when the user removes the suggestion from the mail UI, the row is kept (audit trail, prevent re-suggestion) but flagged DISMISSED, and the associated `Event` is deleted in the same transaction.

**No `Status.NO_MATCH` row**: when the LLM finds nothing, no row is created. The cost: we lose the ability to query "we already checked this mail". The benefit: zero rows for the common case (the vast majority of mails have no event). To prevent re-running, the trigger filters on `new_message_uuids` which by construction is one-shot per sync. If a future "manual re-extract" button is added, a NO_MATCH status can be introduced then.

**Why `GenericForeignKey`**: forward-looking. Today the only `kind` is `EVENT`, with target_content_type pointing to `calendar.Event`. Tomorrow, adding `PACKAGE` (target: `shipping.Package`) or `BOOKING` (target: a future `booking.Reservation`) requires only an enum entry, not a schema migration adding a new nullable FK column. Django's `contenttypes` is already in `INSTALLED_APPS`, no new dependency.

### 3.6 Data model - `Event.source`

Add a `source` discriminator to `workspace.calendar.models.Event`:

```python
class Event(models.Model):
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        ICS = 'ics', 'ICS invitation'
        LLM = 'llm', 'Extracted by AI'
    
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.MANUAL,
    )
```

**Migration backfill**: in the data migration, rows with `ical_uid != ''` become `source=ICS`; the rest become `source=MANUAL`. New LLM-extracted rows get `source=LLM`.

This enables:
- UI badge differentiation in the calendar ("Suggested by AI" vs "Invitation from <organizer>")
- Filter queries (`Event.objects.filter(source=LLM)` for analytics, retention policies)
- Future: per-source deletion policies (e.g., auto-clean dismissed LLM events older than 30 days)

### 3.7 Shared event creation - refactor of `ics_processor`

Extract the event-creation core from `workspace/calendar/services/ics_processor.py` into a new shared module `workspace/calendar/services/event_creation.py`:

```python
def get_or_create_invitation_calendar(user):
    """Returns the 'Invitations' calendar for the user, creating it on
    first use. Moved verbatim from ics_processor.py - the function body
    is unchanged, only the location moves."""

def create_event_from_payload(
    *, user, payload: dict, source: Event.Source,
    source_message: MailMessage | None = None,
    ical_uid: str = '', external_organizer: str = '',
) -> Event:
    """Single path for creating a mail-sourced Event. Used by both
    ICS processing and LLM extraction. Routes to the invitation
    calendar via get_or_create_invitation_calendar.
    
    payload: {'title', 'start', 'end', 'location', 'description', 'all_day'}
    """
```

`ics_processor._handle_request()` shrinks to:
```python
event = create_event_from_payload(
    user=mail.account.owner,
    payload={'title': summary, 'start': dtstart, 'end': dtend, ...},
    source=Event.Source.ICS,
    source_message=mail,
    ical_uid=uid,
    external_organizer=organizer_email,
)
EventMember.objects.create(event=event, user=user, status='pending')
```

LLM path calls the same helper with `source=Source.LLM` and **no** `EventMember` creation (the user is the sole participant - they're not being invited by anyone).

Behavior of ICS processing is unchanged. Verified by re-running `workspace/calendar/tests/test_ics_*.py` after the refactor.

### 3.8 UI

**Mail detail template** (`workspace/mail/ui/templates/mail/ui/partials/message_detail.html`): inject a new section after the AI summary block (around line 213), iterating over the serialized `extractions` field:

```django
{# Server-rendered partial swap pattern, alpine-ajax handles refreshes #}
<template x-for="extraction in messageDetail.extractions" :key="extraction.uuid">
  <div x-show="extraction.kind === 'event' && extraction.status === 'detected'"
       class="mb-5 rounded-lg border border-accent/15 ...">
    <!-- title, datetime, location, "View in calendar" link, "Remove" button -->
  </div>
</template>
```

The widget visually mirrors the existing ICS-invitation widget in the calendar event detail panel so that users see a familiar shape regardless of how the event got there. Difference: no Accept/Decline buttons (the user is the organizer, nothing to accept).

**Serializer change** (`workspace/mail/serializers.py`):
- `MailMessageDetailSerializer` adds `extractions = MailExtractionSerializer(many=True, read_only=True)`.
- The source queryset filters `status=DETECTED` - DISMISSED rows are excluded from API responses (they're audit-only). The mail UI never sees a dismissed extraction.
- `MailExtractionSerializer` exposes `{uuid, kind, target}` where `target` is the embedded serialized `Event` for `kind=event`. `status` is omitted from the response since only DETECTED rows are returned. `confidence`, `raw_output`, `model_used` stay server-side - audit fields, not user-facing.

### 3.9 API endpoints

| Endpoint | Body | Behavior |
|---|---|---|
| `GET /api/v1/mail/messages/<uuid>` (existing) | - | Now includes `extractions: [...]` in the detail response |
| `DELETE /api/v1/mail/extractions/<uuid>` (new) | - | Sets `status=DISMISSED`. If `kind=event` and `target` exists, deletes the `Event` row in the same transaction. Idempotent: dismissing an already-dismissed row returns 204 unchanged. |

No endpoint for re-running extraction in v1 (no UI affordance, no need). Adding `POST /api/v1/mail/messages/<uuid>/extract-event` later is one view + one `dispatch()` call.

### 3.10 Settings & rate limiting

Reuses the existing `mail.ai_enabled` user setting (gating classify, summarize, compose - now also gates extract). No new setting in v1.

Cost ceiling: the per-sync `new_message_uuids` filter already caps the burst at "the messages a single IMAP sync just inserted", which is typically <50. A pathological mass-import (migrating from another mail server) would still respect the cap because IMAP syncs are paginated. If real-world cost is too high, a follow-up can add an opt-in setting `mail.ai_extract_events_enabled` (default true) for finer-grained control.

## 4. Testing

Per the CLAUDE.md "refactor must have test coverage first" rule and the bug-fix regression-test rule:

**Pre-refactor**: write tests covering `ics_processor._handle_request` (specifically the event creation paths it inlines), so the extract-to-`event_creation.py` move is provably no-op. Audit `workspace/calendar/tests/test_ics_*` for coverage; add tests where thin.

**New tests**:
- `workspace/calendar/tests/test_event_creation.py`: unit tests for `create_event_from_payload` covering both `source=ICS` and `source=LLM` paths, calendar routing, source-message linkage.
- `workspace/mail/tests/test_extraction_model.py`: `MailExtraction` round-trips, GenericForeignKey resolution to an `Event`, cascade behavior when MailMessage or Event is deleted.
- `workspace/mail/tests/test_threads.py`: `get_thread()` walks correctly, caps at `max_depth`, returns single message when chain is broken.
- `workspace/ai/tests/test_extract_from_mail.py`: worker happy-path (one valid event), confidence-filter (drops `medium`), past-start filter (drops yesterday's RDV), thread-context inclusion, JSON-parse failure handling, multi-event-per-mail.
- `workspace/mail/tests/test_imap_sync_extract_dispatch.py`: dispatch fires when conditions are met, skipped when `mail.ai_enabled=False` (mirror of existing classify dispatch test).
- `workspace/mail/tests/test_api_extractions.py`: dismiss endpoint deletes the event, 204 on idempotent dismiss, returns 404 for other users' extractions.

## 5. Migrations

Three migrations (numbered by Django auto-increment at write time, here described by content):

1. `mail`: schema migration adding `MailMessage.in_reply_to` (CharField, blank, default ''). No backfill - old rows stay empty and behave as standalone messages in `get_thread()`.
2. `mail`: schema migration creating `MailExtraction` with its indexes. No data migration.
3. `calendar`: schema migration adding `Event.source` (CharField with choices, default `MANUAL`), then a data migration in the same file setting `source='ics'` where `ical_uid != ''`. Single file for ordering predictability.

All three are forward-only; no rollback data is preserved (the additions are non-destructive, removing the field is the natural reversal).

## 6. Phased rollout

This spec is sized for a single PR but the implementation will land in 4 mergeable commits inside it:

1. `feat(mail): store In-Reply-To header on incoming messages` - schema + parser + `get_thread` helper + tests. Self-contained, mergeable on its own if review wants to split.
2. `refactor(calendar): extract event creation into shared service` - move `get_or_create_invitation_calendar` and add `create_event_from_payload`; rewrite `ics_processor._handle_request` to use it. Behavior-preserving; ICS test suite must stay green.
3. `feat(calendar): add Event.source discriminator` - schema + data migration backfilling ICS. Touches `Event` model only; no UI changes yet.
4. `feat(ai): extract calendar events from mail with LLM` - the core feature: `MailExtraction` model, prompt, worker, dispatch wire-up, serializer, mail UI, dismiss endpoint, tests.

Each commit is independently green and reviewable. If something blocks (3) for instance, (1) and (2) still ship value.

## 7. Open questions

- **Time zone handling**: emails rarely state TZ explicitly. The prompt should ask the LLM to assume the user's TZ (`user.profile.timezone` or the account's default). If the email mentions a specific TZ ("3pm EST"), the LLM should preserve it. Edge case: ambiguous "3pm" with no context defaults to user's TZ. Worth a code comment and a test fixture.
- **All-day events**: a "concert le 14 mai" with no time should produce an all-day event. The Pydantic schema has `all_day: bool` - the LLM is instructed to set it when no time is mentioned. Test fixture needed.
- **Recurring events**: explicitly out of scope for v1. The LLM is instructed not to produce recurrence patterns. A future iteration could add `recurrence_rule` to `ExtractedEvent`.

## 8. Future work (post-v1)

- Manual re-extraction endpoint + UI button (when the LLM misses something, or the user dismissed and wants to re-suggest).
- Backfill management command (`uv run python manage.py extract_events --since=7d`) for users opting into a one-time pass over recent mail.
- Per-account model override (route extract calls for "important" accounts to a larger model).
- Additional `kind` values: package tracking (`POST` carriers' "your parcel is at 12 rue X" emails), invoice/receipt extraction, booking reference numbers. The `MailExtraction` schema is ready for these without further changes.
