"""Autonomous agent goal Celery tasks (dispatcher + check-in worker)."""

import logging

from celery import shared_task
from django.utils import timezone

from workspace.ai.metrics import AI_AGENT_CHECKINS
from workspace.ai.services.chat_summary import maybe_dispatch_summary_update
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.ai.services.llm import sanitize_messages_for_storage
from workspace.ai.services.responses import post_bot_message
from workspace.ai.services.tool_loop import run_tool_loop
from workspace.common.celery_claim import cas_finalize, dispatch_due
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@shared_task(name="ai.dispatch_agent_goals")
def dispatch_agent_goals():
    """Find agent goals due for a check-in and dispatch a worker for each.

    Same claim-and-enqueue pattern as ``ai.dispatch_scheduled_messages``;
    only the due predicate differs (active goals whose ``next_check_at``
    has passed).
    """
    from workspace.ai.models import AgentGoal

    due = AgentGoal.objects.filter(
        status=AgentGoal.Status.ACTIVE,
        next_check_at__lte=timezone.now(),
    ).only("pk", "next_check_at")
    outcome = dispatch_due(
        due,
        run_agent_goal_check,
        claim_field="next_check_at",
        extra_where={"status": AgentGoal.Status.ACTIVE},
        label="agent goal check-in",
        log=logger,
    )
    if outcome.dispatched:
        logger.info("Dispatched %d agent goal check-in(s)", outcome.dispatched)
    return outcome.as_dict()


def _build_goal_instruction(goal, user_tz):
    """Build the system-prompt injection describing this check-in."""
    deadline_line = ""
    if goal.deadline:
        deadline_local = goal.deadline.astimezone(user_tz)
        deadline_line = f"- Deadline: {deadline_local.strftime('%Y-%m-%d %H:%M')}\n"

    notes_block = goal.notes or "(none yet — this is your first check-in)"
    elapsed_days = (timezone.now() - goal.created_at).days

    return (
        f"\n\n## Autonomous goal check-in\n"
        f"You are waking up autonomously — the user has NOT sent a message — to work "
        f"on this long-term goal:\n"
        f"- Goal id: {goal.uuid}\n"
        f'- Title: "{goal.title}"\n'
        f"- Objective: {goal.goal}\n"
        f"- Started {elapsed_days} day(s) ago; this is check-in #{goal.check_count + 1}.\n"
        f"{deadline_line}"
        f"- Your private notes from previous check-ins:\n{notes_block}\n\n"
        f"This check-in is SILENT BY DEFAULT. The user sees nothing of what "
        f"happens here: any plain text you write is discarded, never delivered. "
        f"The ONLY way to reach the user is the send_user_message tool.\n\n"
        f"Do the following now:\n"
        f"1. Work on the goal, using your tools when helpful.\n"
        f"2. Save your updated private notes with update_agent_goal (goal_id above) — "
        f"they are your only memory until the next check-in.\n"
        f"3. Choose when to check in next and set it with update_agent_goal's "
        f"next_check_at. If you don't, the next check-in defaults to 24 hours from now.\n"
        f"4. If the goal is achieved or no longer relevant, call complete_agent_goal.\n"
        f"5. Only if you found something genuinely worth telling the user — a "
        f"result, an important change, a deadline at risk, or what the goal says "
        f"to report — call send_user_message with that message, written naturally "
        f"(do not mention check-ins or that you are an automated process). Do NOT "
        f"message just to say you checked and found nothing: staying silent is "
        f"the normal outcome of most check-ins."
    )


@shared_task(name="ai.run_agent_goal_check", bind=True, max_retries=0)
def run_agent_goal_check(self, goal_id: str, claim_token: str | None = None):
    """Run one autonomous check-in: load goal, advance, let the agent work.

    The claim is finalised via :func:`cas_finalize` keyed on the
    dispatcher's ``claim_token`` (same duplicate-delivery protection as
    ``ai.generate_scheduled_response``). The finalize parks
    ``next_check_at`` at ``now + FALLBACK_CHECK_INTERVAL`` *before* the
    LLM run: the agent overrides it by calling update_agent_goal during
    the run, and a crashed or forgetful run resumes in a day instead of
    re-firing immediately or going quiet forever.

    Check-ins are silent by default: the model's final text is discarded
    and only messages explicitly queued through the send_user_message
    tool are posted to the conversation.
    """
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AgentGoal, AITask, BotProfile
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation
    from workspace.chat.services.notifications import notify_new_message
    from workspace.users.services.settings import get_user_timezone

    User = get_user_model()

    try:
        goal = AgentGoal.objects.get(pk=goal_id)
    except AgentGoal.DoesNotExist:
        logger.error("Agent goal not found: %s", scrub(goal_id))
        return {"status": "error", "error": "Goal not found"}

    if goal.status != AgentGoal.Status.ACTIVE:
        return {"status": "skipped", "reason": "not_active"}

    cas_value = claim_token or goal.next_check_at
    now = timezone.now()
    if not cas_finalize(
        AgentGoal,
        goal_id,
        claim_field="next_check_at",
        claim_token=cas_value,
        updates={
            "last_checked_at": now,
            "next_check_at": now + AgentGoal.FALLBACK_CHECK_INTERVAL,
            "check_count": goal.check_count + 1,
        },
        extra_where={"status": AgentGoal.Status.ACTIVE},
    ):
        logger.info(
            "Agent goal check-in skipped (claimed by another worker): goal=%s",
            scrub(goal_id),
        )
        return {"status": "skipped", "reason": "already_claimed"}

    try:
        bot_user = User.objects.get(pk=goal.bot_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=goal.conversation_id)
    except User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist:
        logger.error(
            "Agent goal check-in failed: goal=%s - bot or conversation not found",
            scrub(goal_id),
        )
        return {"status": "error", "error": "Not found"}

    human_user = User.objects.filter(pk=goal.created_by_id).first()
    user_tz = get_user_timezone(human_user or goal.created_by)

    history, summary_text = build_conversation_history(
        str(conversation.pk),
        bot_profile,
        human_user,
    )

    bot_name = bot_user.get_full_name() or bot_user.username

    messages = build_chat_messages(
        bot_profile.system_prompt + _build_goal_instruction(goal, user_tz),
        history,
        bot_name=bot_name,
        user=human_user,
        bot=bot_user,
        summary=summary_text,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.AGENT,
        status=AITask.Status.PROCESSING,
        input_data={
            "goal_id": goal_id,
            "conversation_id": str(conversation.pk),
        },
    )

    try:
        initial_messages = sanitize_messages_for_storage(list(messages))

        # agent_checkin marks the run for send_user_message, which refuses to
        # queue outside a check-in (in normal chat the reply IS the message).
        result, tool_context, rounds, tool_data = run_tool_loop(
            messages,
            bot_profile.get_model(),
            human_user,
            bot_user,
            str(conversation.pk),
            context={"agent_checkin": True},
        )

        raw_messages = {"messages": initial_messages, "rounds": rounds}

        # Check-ins are silent by default: the model's final plain text is
        # never shown to the user. Only messages the agent explicitly queued
        # through send_user_message get delivered.
        queued = [m for m in tool_context.get("agent_messages", []) if m.strip()]
        if not queued:
            ai_task.status = ai_task.Status.COMPLETED
            ai_task.result = "[SILENT]"
            ai_task.model_used = result.get("model", "")
            ai_task.prompt_tokens = result.get("prompt_tokens")
            ai_task.completion_tokens = result.get("completion_tokens")
            ai_task.raw_messages = raw_messages
            ai_task.completed_at = timezone.now()
            ai_task.save()
            AI_AGENT_CHECKINS.labels(outcome="silent").inc()
            logger.info("Agent check-in done silently: goal=%s", scrub(goal_id))
            return {"status": "ok", "reason": "silent"}

        # The user may have paused or stopped the goal while the LLM run was
        # in flight - in that case they asked not to be contacted, so drop
        # the queued messages. When the *agent* closed the goal itself during
        # this run (its tools set the flag below), the final wrap-up message
        # is legitimate and still goes out.
        current_status = (
            AgentGoal.objects.filter(pk=goal_id)
            .values_list("status", flat=True)
            .first()
        )
        if current_status != AgentGoal.Status.ACTIVE and not tool_context.get(
            "agent_goal_changed"
        ):
            ai_task.status = ai_task.Status.COMPLETED
            ai_task.result = "[SUPPRESSED]"
            ai_task.model_used = result.get("model", "")
            ai_task.prompt_tokens = result.get("prompt_tokens")
            ai_task.completion_tokens = result.get("completion_tokens")
            ai_task.raw_messages = raw_messages
            ai_task.completed_at = timezone.now()
            ai_task.save()
            AI_AGENT_CHECKINS.labels(outcome="suppressed").inc()
            logger.info(
                "Agent check-in message suppressed (goal closed by user mid-run): "
                "goal=%s status=%s",
                scrub(goal_id),
                current_status,
            )
            return {"status": "skipped", "reason": "goal_closed_by_user"}

        result_to_post = {**result, "content": "\n\n".join(queued)}
        body, bot_message = post_bot_message(
            conversation,
            bot_user,
            result_to_post,
            tool_context,
            ai_task,
            raw_messages,
            tool_data=tool_data,
        )

        notify_new_message(conversation, bot_user, body)

        maybe_dispatch_summary_update(str(conversation.pk), summary_text)

        AI_AGENT_CHECKINS.labels(outcome="message").inc()
        logger.info(
            "Agent check-in posted a message: goal=%s conversation=%s tokens=%s+%s",
            scrub(goal_id),
            scrub(conversation.pk),
            result.get("prompt_tokens"),
            result.get("completion_tokens"),
        )
        return {"status": "ok", "message_id": str(bot_message.uuid)}

    except Exception as e:
        AI_AGENT_CHECKINS.labels(outcome="error").inc()
        # Unlike chat and scheduled generation, a failed check-in must not
        # post an error message: the user never asked for this run, and the
        # silent-default contract holds on failure too. The fallback
        # next_check_at written at claim time already schedules the retry.
        ai_task.status = ai_task.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        logger.exception("Agent goal check-in failed: goal=%s", scrub(goal_id))
        return {"status": "error", "error": str(e)}
