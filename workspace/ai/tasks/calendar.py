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

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ExtractedEvent(BaseModel):
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    confidence: Literal["high", "medium", "low"]
    reasoning: str = ""


@shared_task(name="ai.extract_from_mail", bind=True, max_retries=0)
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
    result = call_llm(messages, model=model)

    raw_content = _FENCE_RE.sub("", (result.get("content") or "").strip())
    try:
        items = orjson.loads(raw_content)
    except (ValueError, TypeError):
        logger.warning("Extract: malformed JSON for message %s", scrub(str(msg.pk)))
        return {
            "count": 0,
            "prompt_tokens": result.get("prompt_tokens", 0) or 0,
            "completion_tokens": result.get("completion_tokens", 0) or 0,
            "model": result.get("model", ""),
        }
    if not isinstance(items, list):
        logger.warning("Extract: expected JSON array, got %s", type(items).__name__)
        return {
            "count": 0,
            "prompt_tokens": result.get("prompt_tokens", 0) or 0,
            "completion_tokens": result.get("completion_tokens", 0) or 0,
            "model": result.get("model", ""),
        }

    created = 0
    now = dj_tz.now()
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        try:
            extracted = ExtractedEvent.model_validate(raw_item)
        except ValidationError:
            logger.debug("Extract: invalid event shape from LLM, dropping")
            continue

        if extracted.confidence != "high":
            logger.debug("Extract: dropping non-high confidence event")
            continue
        if extracted.start <= now:
            logger.debug("Extract: dropping past-dated event")
            continue

        with transaction.atomic():
            event = create_event_from_payload(
                user=msg.account.owner,
                payload={
                    "title": extracted.title,
                    "start": extracted.start,
                    "end": extracted.end,
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
                raw_output=raw_item,
            )
        created += 1

    return {
        "count": created,
        "prompt_tokens": result.get("prompt_tokens", 0) or 0,
        "completion_tokens": result.get("completion_tokens", 0) or 0,
        "model": result.get("model", ""),
    }
