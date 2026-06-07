"""Scheduled bot message Celery tasks (dispatcher + worker)."""

import logging

from celery import shared_task
from django.utils import timezone

from workspace.ai.services.chat_summary import maybe_dispatch_summary_update
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.ai.services.llm import (
    clean_llm_content,
    sanitize_messages_for_storage,
)
from workspace.ai.services.responses import handle_generation_error, post_bot_message
from workspace.ai.services.tool_loop import retry_final_completion, run_tool_loop
from workspace.common.celery_claim import cas_claim, cas_finalize, cas_rollback
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@shared_task(name="ai.dispatch_scheduled_messages")
def dispatch_scheduled_messages():
    """Find due scheduled messages and dispatch a generation task for each.

    Each row is CAS-claimed by advancing ``next_run_at`` past the due
    window (see :mod:`workspace.common.celery_claim`). Only the
    dispatcher whose UPDATE affected a row enqueues the worker;
    concurrent dispatcher runs (or beat-fired duplicates) race on the
    same predicate and the database guarantees one winner per row. The
    token is passed to the worker so its own CAS can pin against the
    exact value we just wrote.
    """
    from workspace.ai.models import ScheduledMessage

    now = timezone.now()
    due = ScheduledMessage.objects.filter(
        is_active=True,
        next_run_at__lte=now,
    ).only("pk", "next_run_at")
    count = 0
    for schedule in due:
        original_next_run_at = schedule.next_run_at
        token = cas_claim(
            ScheduledMessage,
            schedule.pk,
            claim_field="next_run_at",
            observed_value=original_next_run_at,
            extra_where={"is_active": True},
        )
        if token is None:
            continue
        try:
            generate_scheduled_response.delay(
                str(schedule.uuid),
                token.isoformat(),
            )
        except Exception:
            # Broker errors etc. - roll back the claim so the row stays
            # due and re-fires on the next dispatcher pass instead of
            # being parked at the token for the lock horizon. Keep
            # looping so other due rows still get a chance.
            cas_rollback(
                ScheduledMessage,
                schedule.pk,
                "next_run_at",
                original_next_run_at,
            )
            logger.exception(
                "Failed to enqueue scheduled response: schedule=%s",
                scrub(str(schedule.pk)),
            )
            continue
        count += 1
    if count:
        logger.info("Dispatched %d scheduled message(s)", count)


@shared_task(name="ai.generate_scheduled_response", bind=True, max_retries=0)
def generate_scheduled_response(self, schedule_id: str, claim_token: str | None = None):
    """Run a scheduled bot message: load schedule, advance, generate.

    The claim is finalised via
    :func:`workspace.common.celery_claim.cas_finalize` keyed on the
    dispatcher's ``claim_token``. Duplicate Celery deliveries whose row
    has been advanced past the token by the winning worker fail the
    CAS and return ``skipped/already_claimed`` before re-posting.

    Calls without a token (legacy queued tasks, direct test calls) fall
    back to CAS against the value observed at load time — still good
    enough to block the in-flight worker race against the dispatcher's
    window.

    The bot may emit ``[SKIP]`` to indicate the scheduled action is no
    longer relevant; no message is posted in that case.
    """
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AITask, BotProfile, ScheduledMessage
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation
    from workspace.chat.services.notifications import notify_new_message
    from workspace.users.services.settings import get_user_timezone

    User = get_user_model()

    try:
        schedule = ScheduledMessage.objects.get(pk=schedule_id)
    except ScheduledMessage.DoesNotExist:
        logger.error("Scheduled message not found: %s", scrub(schedule_id))
        return {"status": "error", "error": "Schedule not found"}

    if not schedule.is_active:
        return {"status": "skipped", "reason": "inactive"}

    creator_tz = get_user_timezone(schedule.created_by)

    # Capture the value the CAS will pin against *before* compute_next_run
    # mutates schedule.next_run_at — when there is no claim_token, the
    # fallback predicate is the pre-advance value.
    cas_value = claim_token or schedule.next_run_at
    schedule.last_run_at = timezone.now()
    schedule.compute_next_run(user_tz=creator_tz)
    if not cas_finalize(
        ScheduledMessage,
        schedule_id,
        claim_field="next_run_at",
        claim_token=cas_value,
        updates={
            "last_run_at": schedule.last_run_at,
            "next_run_at": schedule.next_run_at,
            "is_active": schedule.is_active,
        },
        extra_where={"is_active": True},
    ):
        logger.info(
            "Scheduled response skipped (claimed by another worker): schedule=%s",
            scrub(schedule_id),
        )
        return {"status": "skipped", "reason": "already_claimed"}

    try:
        bot_user = User.objects.get(pk=schedule.bot_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=schedule.conversation_id)
    except User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist:
        logger.error(
            "Scheduled response failed: schedule=%s - bot or conversation not found",
            scrub(schedule_id),
        )
        return {"status": "error", "error": "Not found"}

    human_user = User.objects.filter(pk=schedule.created_by_id).first()

    history, summary_text = build_conversation_history(
        str(conversation.pk),
        bot_profile,
        human_user,
    )

    bot_name = bot_user.get_full_name() or bot_user.username

    # Inject the scheduled action instruction into the system prompt.
    scheduled_instruction = (
        f"\n\n## Scheduled action\n"
        f"You previously scheduled a proactive message with the following instruction:\n"
        f'"{schedule.prompt}"\n\n'
        f"Now is the time to act on it. Generate an appropriate message for the user.\n"
        f"Be natural - do not mention that you are a scheduled message.\n"
        f"If, based on the conversation context, you judge that this message is no longer "
        f"relevant or useful (e.g. the topic was already addressed, the event has passed, "
        f'the user already handled it), reply with exactly "[SKIP]" and nothing else.'
    )

    messages = build_chat_messages(
        bot_profile.system_prompt + scheduled_instruction,
        history,
        bot_name=bot_name,
        user=human_user,
        bot=bot_user,
        summary=summary_text,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={
            "schedule_id": schedule_id,
            "conversation_id": str(conversation.pk),
        },
    )

    try:
        initial_messages = sanitize_messages_for_storage(list(messages))

        result, used_tools, tool_context, rounds, tool_data = run_tool_loop(
            messages,
            bot_profile.get_model(),
            human_user,
            bot_user,
            str(conversation.pk),
        )

        # Auto-retry once if the model returned an empty response.
        # Only the final completion is retried (no tools): rerunning
        # the whole loop would re-execute side-effectful tools and
        # discard the first pass's tool_context / used_tools.
        body_preview = clean_llm_content(result.get("content") or "")
        if not body_preview and not tool_context.get("images"):
            logger.warning(
                "Empty scheduled response, retrying once: schedule=%s",
                scrub(schedule_id),
            )
            result, retry_rounds = retry_final_completion(
                messages, bot_profile.get_model()
            )
            rounds.extend(retry_rounds)
            body_preview = clean_llm_content(result.get("content") or "")
            if not body_preview and not tool_context.get("images"):
                ai_task.status = ai_task.Status.COMPLETED
                ai_task.result = "[EMPTY]"
                ai_task.model_used = result.get("model", "")
                ai_task.prompt_tokens = result.get("prompt_tokens")
                ai_task.completion_tokens = result.get("completion_tokens")
                ai_task.completed_at = timezone.now()
                ai_task.save()
                logger.warning(
                    "Scheduled response empty after retry: schedule=%s",
                    scrub(schedule_id),
                )
                return {"status": "skipped", "reason": "empty_response"}

        raw_messages = {"messages": initial_messages, "rounds": rounds}

        # Let the bot skip if it judges the message is no longer relevant.
        body = clean_llm_content(result["content"])
        if body == "[SKIP]":
            ai_task.status = ai_task.Status.COMPLETED
            ai_task.result = "[SKIP]"
            ai_task.model_used = result["model"]
            ai_task.prompt_tokens = result["prompt_tokens"]
            ai_task.completion_tokens = result["completion_tokens"]
            ai_task.raw_messages = raw_messages
            ai_task.completed_at = timezone.now()
            ai_task.save()
            logger.info(
                "Scheduled response skipped (bot judged irrelevant): schedule=%s",
                scrub(schedule_id),
            )
            return {"status": "skipped", "reason": "bot_judged_irrelevant"}

        body, bot_message = post_bot_message(
            conversation,
            bot_user,
            result,
            used_tools,
            tool_context,
            ai_task,
            raw_messages,
            tool_data=tool_data,
        )

        notify_new_message(conversation, bot_user, body)

        maybe_dispatch_summary_update(str(conversation.pk), summary_text)

        logger.info(
            "Scheduled response generated: schedule=%s conversation=%s tokens=%s+%s",
            scrub(schedule_id),
            scrub(conversation.pk),
            result["prompt_tokens"],
            result["completion_tokens"],
        )
        return {"status": "ok", "message_id": str(bot_message.uuid)}

    except Exception as e:
        logger.exception("Scheduled response failed: schedule=%s", scrub(schedule_id))
        handle_generation_error(conversation, bot_user, ai_task, e)
        return {"status": "error", "error": str(e)}
