"""Mail-related AI Celery tasks (summarize, compose, classify)."""

import logging
import re
from collections import defaultdict
from itertools import batched

import orjson
from celery import shared_task
from django.conf import settings
from django.db import transaction

from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import (
    call_llm,
    sanitize_messages_for_storage,
    serialize_response,
)
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Tolerate LLM outputs wrapped in ``` / ```json fences instead of strict JSON.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.DOTALL)


@shared_task(name="ai.summarize", bind=True, max_retries=0)
def summarize(self, task_id: str):
    """Summarize a single mail message and persist the result.

    Loads the AITask, fetches the referenced MailMessage, calls the LLM
    with the small model, then writes the summary back to both the AITask
    (for history) and the MailMessage.ai_summary field (for display).
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_summarize_messages
    from workspace.mail.models import MailMessage

    try:
        with ai_task_lifecycle(task_id, log_label="Summarize") as ai_task:
            try:
                message = MailMessage.objects.get(
                    pk=ai_task.input_data["message_id"],
                    account__owner=ai_task.owner,
                )
            except MailMessage.DoesNotExist:
                ai_task.status = AITask.Status.FAILED
                ai_task.error = "Mail message not found"
                return {"status": "error", "error": "Mail message not found"}

            body = message.body_text or message.body_html or ""
            messages = build_summarize_messages(message.subject or "", body)
            result = call_llm(messages, model=settings.AI_SMALL_MODEL)

            with transaction.atomic():
                ai_task.result = result["content"]
                ai_task.model_used = result["model"]
                ai_task.prompt_tokens = result["prompt_tokens"]
                ai_task.completion_tokens = result["completion_tokens"]
                ai_task.raw_messages = {
                    "messages": sanitize_messages_for_storage(messages),
                    "response": serialize_response(result),
                }
                # ``ai_task_lifecycle`` will set status=COMPLETED + completed_at
                # on context exit. We need to save the message inside the
                # atomic block though.
                message.ai_summary = result["content"]
                message.save(update_fields=["ai_summary"])

            logger.info(
                "Summarize complete: task=%s tokens=%s+%s",
                scrub(task_id),
                result["prompt_tokens"],
                result["completion_tokens"],
            )
            return {"status": "ok", "task_id": task_id}
    except AITask.DoesNotExist:
        logger.error("Summarize task not found: %s", scrub(task_id))
        return {"status": "error", "error": "Task not found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@shared_task(name="ai.compose_email", bind=True, max_retries=0)
def compose_email(self, task_id: str):
    """Compose a new email or generate a reply to an existing one.

    Resolves the sender identity from the requested mail account (or falls
    back to the user profile), builds the appropriate prompt (compose vs
    reply), then writes the LLM result back to the AITask for the UI to
    poll.
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_compose_messages, build_reply_messages
    from workspace.mail.models import MailAccount, MailMessage

    try:
        with ai_task_lifecycle(task_id, log_label="Compose") as ai_task:
            instructions = ai_task.input_data.get("instructions", "")
            original_message_id = ai_task.input_data.get("message_id")

            # Resolve sender identity from the mail account or user profile.
            sender_name = ""
            sender_email = ""
            account_id = ai_task.input_data.get("account_id")
            if account_id:
                account = MailAccount.objects.filter(
                    pk=account_id, owner=ai_task.owner
                ).first()
                if account:
                    sender_name = account.display_name
                    sender_email = account.email
            if not sender_email:
                sender_name = ai_task.owner.get_full_name()
                sender_email = ai_task.owner.email or ""

            if original_message_id:
                message = MailMessage.objects.select_related("account").get(
                    pk=original_message_id,
                    account__owner=ai_task.owner,
                )
                body = message.body_text or message.body_html or ""
                # Use the account from the original message for reply.
                reply_name = message.account.display_name or sender_name
                reply_email = message.account.email or sender_email
                messages = build_reply_messages(
                    instructions,
                    message.subject or "",
                    body,
                    sender_name=reply_name,
                    sender_email=reply_email,
                )
            else:
                context = ai_task.input_data.get("context", "")
                messages = build_compose_messages(
                    instructions,
                    context,
                    sender_name=sender_name,
                    sender_email=sender_email,
                )

            result = call_llm(messages)
            ai_task.result = result["content"]
            ai_task.model_used = result["model"]
            ai_task.prompt_tokens = result["prompt_tokens"]
            ai_task.completion_tokens = result["completion_tokens"]
            ai_task.raw_messages = {
                "messages": sanitize_messages_for_storage(messages),
                "response": serialize_response(result),
            }

            logger.info(
                "Compose complete: task=%s tokens=%s+%s",
                scrub(task_id),
                result["prompt_tokens"],
                result["completion_tokens"],
            )
            return {"status": "ok", "task_id": task_id}
    except AITask.DoesNotExist:
        logger.error("Compose task not found: %s", scrub(task_id))
        return {"status": "error", "error": "Task not found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


CLASSIFY_BATCH_SIZE = 10
MAX_LABELS_PER_MESSAGE = 3


@shared_task(name="ai.classify_mail", bind=True, max_retries=0)
def classify_mail_messages(self, task_id: str):
    """Classify a batch of mail messages by assigning labels.

    Reads ``message_uuids`` from ``AITask.input_data``, groups them by
    mail account, and submits each account's messages to the LLM in
    fixed-size batches with the account's label set as the candidate
    list. Each message can receive up to ``MAX_LABELS_PER_MESSAGE``.
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_classify_messages
    from workspace.mail.models import MailLabel, MailMessage, MailMessageLabel

    try:
        with ai_task_lifecycle(task_id, log_label="Classify") as ai_task:
            message_uuids = ai_task.input_data.get("message_uuids", [])
            by_uuid = {
                str(m.uuid): m
                for m in MailMessage.objects.filter(
                    uuid__in=message_uuids,
                    account__owner=ai_task.owner,
                ).only("uuid", "subject", "from_address", "snippet", "account_id")
            }
            # Preserve the caller's input order. The DB returns rows in
            # PK (uuid) order which is random for v4 UUIDs, so the LLM
            # index (i=1, i=2, ...) would otherwise map to the wrong
            # messages when callers expect input-order semantics.
            msgs = [by_uuid[u] for u in message_uuids if u in by_uuid]

            if not msgs:
                ai_task.result = "No messages to classify"
                return {"status": "ok", "task_id": task_id}

            msgs_by_account = defaultdict(list)
            for m in msgs:
                msgs_by_account[m.account_id].append(m)

            total_prompt = 0
            total_completion = 0
            model_used = ""
            # Collect every label assignment first; commit them in a single
            # transaction at the end so a later batch failing on bad JSON does
            # not leave half the messages partially labelled.
            all_links = []

            for account_id, account_msgs in msgs_by_account.items():
                account_labels = list(MailLabel.objects.filter(account_id=account_id))
                label_names = [lbl.name for lbl in account_labels]
                label_by_lower = {lbl.name.lower(): lbl for lbl in account_labels}

                for batch in batched(account_msgs, CLASSIFY_BATCH_SIZE, strict=False):
                    uuid_index = {i + 1: m for i, m in enumerate(batch)}

                    emails = []
                    for m in batch:
                        from_addr = (
                            m.from_address if isinstance(m.from_address, dict) else {}
                        )
                        emails.append(
                            {
                                "subject": m.subject or "",
                                "from_name": from_addr.get("name", ""),
                                "from_email": from_addr.get("email", ""),
                                "snippet": m.snippet or "",
                            }
                        )

                    messages = build_classify_messages(emails, label_names)
                    result = call_llm(messages, model=settings.AI_SMALL_MODEL)

                    model_used = result["model"]
                    total_prompt += result["prompt_tokens"] or 0
                    total_completion += result["completion_tokens"] or 0

                    # Tolerate ```json fences and stray whitespace - small models
                    # often emit fenced code blocks despite the prompt asking for
                    # plain JSON.
                    raw_content = _FENCE_RE.sub("", (result["content"] or "").strip())
                    try:
                        items = orjson.loads(raw_content)
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            "Classify: malformed JSON response for task %s",
                            scrub(task_id),
                        )
                        raise ValueError("Malformed JSON response from LLM") from e

                    if not isinstance(items, list):
                        raise ValueError("Expected JSON array from LLM")

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        idx = item.get("i")
                        raw_labels = item.get("labels", [])

                        msg = uuid_index.get(idx)
                        if not msg:
                            continue

                        if not isinstance(raw_labels, list):
                            continue

                        count = 0
                        for raw_name in raw_labels:
                            if count >= MAX_LABELS_PER_MESSAGE:
                                break
                            if not isinstance(raw_name, str):
                                continue
                            label = label_by_lower.get(raw_name.lower())
                            if label:
                                all_links.append(
                                    MailMessageLabel(message=msg, label=label)
                                )
                                count += 1

            with transaction.atomic():
                if all_links:
                    MailMessageLabel.objects.bulk_create(
                        all_links, ignore_conflicts=True
                    )
                ai_task.result = f"Classified {len(msgs)} messages"
                ai_task.model_used = model_used
                ai_task.prompt_tokens = total_prompt
                ai_task.completion_tokens = total_completion

            logger.info(
                "Classify complete: task=%s messages=%d tokens=%d+%d",
                scrub(task_id),
                len(msgs),
                total_prompt,
                total_completion,
            )
            return {"status": "ok", "task_id": task_id}
    except AITask.DoesNotExist:
        logger.error("Classify task not found: %s", scrub(task_id))
        return {"status": "error", "error": "Task not found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
