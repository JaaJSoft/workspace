"""LLM-based extraction of calendar events from mail.

For each new mail synced in a batch, reconstruct the thread, call the
LLM with a strict event-extraction prompt, validate the response, and
materialize an Event + MailExtraction for each high-confidence,
future-dated entry. Per-message transactions: one bad mail does not
poison the batch.

Every new mail of a thread sends the whole thread again, so the model
keeps seeing events it already reported - under a new title more often
than not. An extracted event is therefore identified by its start, never
by its title: see _previous_event.
"""

import logging
from datetime import UTC, datetime, time
from typing import Literal

from celery import shared_task
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone as dj_tz
from pydantic import BaseModel

from workspace.ai.models import AITask
from workspace.ai.prompts.calendar import build_event_extraction_messages
from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import call_llm_structured
from workspace.calendar.models import Event
from workspace.calendar.services.event_creation import create_event_from_payload
from workspace.common.logging import scrub
from workspace.common.task_priority import NORMAL_PRIORITY
from workspace.mail.models import MailExtraction, MailMessage
from workspace.mail.services.threads import get_thread

logger = logging.getLogger(__name__)


class ExtractedEvent(BaseModel):
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    confidence: Literal["high", "medium", "low"]
    reasoning: str = ""


class ExtractedEvents(BaseModel):
    """Envelope: the json_schema response format requires a top-level object."""

    events: list[ExtractedEvent]


@shared_task(
    name="ai.extract_from_mail", priority=NORMAL_PRIORITY, bind=True, max_retries=0
)
def extract_from_mail_messages(self, task_id: str):
    """Run LLM event extraction over the messages referenced by `task_id`.

    Errors on a per-message basis are logged and skipped: the task as a
    whole still reaches COMPLETED unless ai_task_lifecycle catches a
    fatal exception.
    """
    try:
        with ai_task_lifecycle(task_id, log_label="Extract") as ai_task:
            message_uuids = ai_task.input_data.get("message_uuids", [])
            msgs = list(
                MailMessage.objects.filter(
                    uuid__in=message_uuids,
                    account__owner=ai_task.owner,
                ).select_related("account")
            )

            if not msgs:
                ai_task.result = "No messages to extract from"
                return {"status": "ok", "task_id": task_id}

            total_prompt = 0
            total_completion = 0
            model_used = ""
            event_ct = ContentType.objects.get_for_model(Event)
            extractions_created = 0

            for msg in msgs:
                try:
                    created = _extract_one_message(msg, event_ct)
                    extractions_created += created["count"]
                    total_prompt += created["prompt_tokens"]
                    total_completion += created["completion_tokens"]
                    if created["model"]:
                        model_used = created["model"]
                except Exception:
                    logger.exception(
                        "Extract: failed for message %s", scrub(str(msg.pk))
                    )

            ai_task.result = (
                f"Created {extractions_created} extractions from {len(msgs)} messages"
            )
            ai_task.model_used = model_used
            ai_task.prompt_tokens = total_prompt
            ai_task.completion_tokens = total_completion

            return {"status": "ok", "task_id": task_id}
    except AITask.DoesNotExist:
        logger.warning("Extract: AITask %s not found", scrub(task_id))
        return {"status": "error", "task_id": task_id}


def _extract_one_message(msg: MailMessage, event_ct: ContentType) -> dict:
    from workspace.users.services.settings import get_user_timezone

    thread = get_thread(msg)
    user_tz = get_user_timezone(msg.account.owner)
    messages = build_event_extraction_messages(thread, user_tz=user_tz)
    model = settings.AI_EXTRACT_MODEL or settings.AI_MODEL
    parsed, result = call_llm_structured(messages, ExtractedEvents, model=model)

    usage = {
        "prompt_tokens": result.get("prompt_tokens", 0) or 0,
        "completion_tokens": result.get("completion_tokens", 0) or 0,
        "model": result.get("model", ""),
    }
    if parsed is None:
        logger.warning(
            "Extract: unparseable LLM output for message %s", scrub(str(msg.pk))
        )
        return {"count": 0, **usage}

    created = 0
    now = dj_tz.now()
    for extracted in parsed.events:
        if extracted.confidence != "high":
            logger.debug("Extract: dropping non-high confidence event")
            continue
        if extracted.start <= now:
            logger.debug("Extract: dropping past-dated event")
            continue

        start, end = extracted.start, extracted.end
        if extracted.all_day:
            start = _all_day_anchor(start)
            end = end and _all_day_anchor(end)

        found, previous = _previous_event(msg, thread, extracted.all_day, start)
        if found:
            if previous is not None:
                created += _link_extraction(msg, previous, extracted, result, event_ct)
            continue

        with transaction.atomic():
            event = create_event_from_payload(
                user=msg.account.owner,
                payload={
                    "title": extracted.title,
                    "start": start,
                    "end": end,
                    "all_day": extracted.all_day,
                    "location": extracted.location,
                    "description": extracted.description,
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
                model_used=result.get("model", ""),
                raw_output=extracted.model_dump(mode="json"),
            )
        created += 1

    return {"count": created, **usage}


def _all_day_anchor(value: datetime) -> datetime:
    """UTC midnight of the day the model wrote, as every all-day event is
    stored (calendar/services/timezones.py). The model writes a local
    midnight, which east of UTC is still the previous UTC day."""
    return datetime.combine(value.date(), time.min, tzinfo=UTC)


def _is_same_moment(raw: dict, all_day: bool, start: datetime) -> bool:
    try:
        raw_start = datetime.fromisoformat(raw["start"])
    except KeyError, TypeError, ValueError:
        return False
    if bool(raw.get("all_day")) != all_day:
        return False
    if all_day:
        raw_start = _all_day_anchor(raw_start)
    return raw_start == start


def _previous_event(
    msg: MailMessage,
    thread: list[MailMessage],
    all_day: bool,
    start: datetime,
) -> tuple[bool, Event | None]:
    """Whether the event starting at `start` was already reported, and the
    event that stands for it when that event still exists.

    The thread's own extractions come first: when the user deleted or
    dismissed what an earlier mail produced, or moved it to another time,
    a reply quoting that mail must not bring it back. Outside the thread,
    a reminder that does not reply to the confirmation, or the invitation
    the same mail carried as an ICS, already put the event in the calendar.
    """
    thread_extractions = MailExtraction.objects.filter(
        mail_message__in=thread, kind=MailExtraction.Kind.EVENT
    ).order_by("created_at")
    for extraction in thread_extractions:
        if _is_same_moment(extraction.raw_output or {}, all_day, start):
            alive = extraction.status != MailExtraction.Status.DISMISSED
            return True, extraction.target if alive else None

    existing = (
        Event.objects.filter(
            owner=msg.account.owner,
            all_day=all_day,
            start=start,
            source__in=(Event.Source.LLM, Event.Source.ICS),
            is_cancelled=False,
            recurrence_parent__isnull=True,
        )
        .order_by("created_at")
        .first()
    )
    return existing is not None, existing


def _link_extraction(
    msg: MailMessage,
    event: Event,
    extracted: ExtractedEvent,
    result: dict,
    event_ct: ContentType,
) -> int:
    """Attach `msg` to an event an earlier mail produced, so the mail still
    shows what it is about. Returns the number of rows created."""
    _, created = MailExtraction.objects.get_or_create(
        mail_message=msg,
        kind=MailExtraction.Kind.EVENT,
        target_content_type=event_ct,
        target_object_id=event.uuid,
        defaults={
            "confidence": extracted.confidence,
            "model_used": result.get("model", ""),
            "raw_output": extracted.model_dump(mode="json"),
        },
    )
    return int(created)
