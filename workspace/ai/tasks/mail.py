"""Mail-related AI Celery tasks (summarize, compose, classify)."""

import logging
from collections import defaultdict
from itertools import batched

from celery import shared_task
from django.conf import settings
from django.db import transaction
from pydantic import BaseModel

from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import (
    call_llm,
    call_llm_structured,
    sanitize_messages_for_storage,
    serialize_response,
)
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


class LabelAssignment(BaseModel):
    i: int
    labels: list[str] = []


class ClassifiedEmails(BaseModel):
    """Envelope: the json_schema response format requires a top-level object."""

    results: list[LabelAssignment]


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


def _classify_message_queryset(owner, message_uuids):
    """Messages to classify, with the fields the notify-on-apply path needs.

    Shared with the test suite so the query-shape regression test (folder
    fields must be selected via ``folder__x``, not deferred-loaded one at a
    time) pins the queryset that actually ships, not a copy of it.
    """
    from workspace.mail.models import MailMessage

    return (
        MailMessage.objects.filter(
            uuid__in=message_uuids,
            account__owner=owner,
            # The task is queued, so a message can be soft-deleted between
            # dispatch and the LLM call returning. Excluding it here is the only
            # place that can: the notify path reads these rows through .only(),
            # which does not load deleted_at.
            deleted_at__isnull=True,
        )
        .select_related("folder", "account")
        .only(
            "uuid",
            "subject",
            "from_name",
            "from_email",
            "snippet",
            "reply_to",
            "to_addresses",
            "cc_addresses",
            "date",
            "in_reply_to",
            "has_attachments",
            "has_calendar_event",
            "is_read",
            "account_id",
            "account__email",
            "folder_id",
            "folder__name",
            "folder__display_name",
            "folder__folder_type",
            "folder__is_hidden",
        )
    )


def _classify_payload(message, account_email, user_tz):
    """The message fields handed to the classifier prompt.

    Beyond sender and subject, the classifier gets the metadata that separates a
    personal message from a broadcast - how the owner was addressed, a Reply-To
    pointing elsewhere, the folder the server filed it in, attachments and
    whether it continues a thread.
    """
    from workspace.mail.services.addresses import recipient_summary

    role, recipient_count = recipient_summary(
        account_email, message.to_addresses, message.cc_addresses
    )
    return {
        "subject": message.subject or "",
        "from_name": message.from_name,
        "from_email": message.from_email,
        "snippet": message.snippet or "",
        "reply_to": message.reply_to,
        "recipient_role": role,
        "recipient_count": recipient_count,
        "date": message.date.astimezone(user_tz) if message.date else None,
        "folder": message.folder.display_name or message.folder.name,
        "has_attachments": message.has_attachments,
        "has_calendar_event": message.has_calendar_event,
        "is_reply": bool(message.in_reply_to),
    }


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
    from workspace.mail.models import MailLabel, MailMessageLabel
    from workspace.users.services.settings import get_user_timezone

    try:
        with ai_task_lifecycle(task_id, log_label="Classify") as ai_task:
            message_uuids = ai_task.input_data.get("message_uuids", [])
            by_uuid = {
                str(m.uuid): m
                for m in _classify_message_queryset(ai_task.owner, message_uuids)
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

            user_tz = get_user_timezone(ai_task.owner)

            for account_id, account_msgs in msgs_by_account.items():
                account_email = account_msgs[0].account.email
                account_labels = list(MailLabel.objects.filter(account_id=account_id))
                label_names = [lbl.name for lbl in account_labels]
                label_by_lower = {lbl.name.lower(): lbl for lbl in account_labels}

                for batch in batched(account_msgs, CLASSIFY_BATCH_SIZE, strict=False):
                    uuid_index = {i + 1: m for i, m in enumerate(batch)}

                    emails = [
                        _classify_payload(m, account_email, user_tz) for m in batch
                    ]

                    messages = build_classify_messages(emails, label_names)
                    parsed, result = call_llm_structured(
                        messages, ClassifiedEmails, model=settings.AI_SMALL_MODEL
                    )

                    model_used = result["model"]
                    total_prompt += result["prompt_tokens"] or 0
                    total_completion += result["completion_tokens"] or 0

                    if parsed is None:
                        logger.warning(
                            "Classify: malformed JSON response for task %s",
                            scrub(task_id),
                        )
                        raise ValueError("Malformed JSON response from LLM")

                    for item in parsed.results:
                        msg = uuid_index.get(item.i)
                        if not msg:
                            continue

                        count = 0
                        for raw_name in item.labels:
                            if count >= MAX_LABELS_PER_MESSAGE:
                                break
                            label = label_by_lower.get(raw_name.lower())
                            if label:
                                all_links.append(
                                    MailMessageLabel(message=msg, label=label)
                                )
                                count += 1

            with transaction.atomic():
                if all_links:
                    # Callers only ever pass unlabelled messages, so all_links
                    # holds new assignments only; ignore_conflicts is a race
                    # guard, not a sign of a message being re-notified.
                    MailMessageLabel.objects.bulk_create(
                        all_links, ignore_conflicts=True
                    )
                ai_task.result = f"Classified {len(msgs)} messages"
                ai_task.model_used = model_used
                ai_task.prompt_tokens = total_prompt
                ai_task.completion_tokens = total_completion

            # Outside the atomic block on purpose: notify() dispatches the push
            # task immediately, and inside an open transaction the worker can
            # run before the rows are visible and drop the push silently.
            _notify_for_notifying_labels(ai_task, all_links)

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


def _notify_for_notifying_labels(ai_task, links):
    """Push a notification for each message that got a notify_on_apply label.

    Called from the task rather than from a post_save signal on
    MailMessageLabel: a signal would also fire when the user labels their own
    message by hand, notifying them about their own action. The same reason
    is why a manually-dispatched classify task sets suppress_notifications.
    """
    from workspace.mail.services.notifications import notify_labeled_messages

    by_pk = {
        link.message.pk: link.message for link in links if link.label.notify_on_apply
    }
    if not by_pk:
        return
    try:
        notify_labeled_messages(
            ai_task.owner,
            list(by_pk.values()),
            was_initial_sync=bool(ai_task.input_data.get("suppress_notifications")),
        )
    except Exception:
        logger.exception(
            "Failed to send mail label notifications for task %s",
            scrub(str(ai_task.pk)),
        )
