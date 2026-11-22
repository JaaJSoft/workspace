"""Editor AI Celery tasks (improve, explain, summarize, custom)."""

import logging

from celery import shared_task
from django.utils.translation import gettext

from workspace.ai.services.ai_task import (
    TaskNotPending,
    ai_task_lifecycle,
    record_prompt_run,
)
from workspace.ai.services.llm import run_prompt
from workspace.common.logging import scrub
from workspace.common.task_priority import INTERACTIVE_PRIORITY

logger = logging.getLogger(__name__)


@shared_task(
    name="ai.editor_action", priority=INTERACTIVE_PRIORITY, bind=True, max_retries=0
)
def editor_action(self, task_id: str):
    """Run an AI action on editor content (improve, explain, summarize, custom).

    Reads the requested action and content from ``AITask.input_data``,
    builds the appropriate prompt, calls the LLM, and writes the result.
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.editor import (
        build_custom_messages,
        build_explain_messages,
        build_improve_messages,
        build_summarize_messages,
    )

    try:
        with ai_task_lifecycle(task_id, log_label="Editor action") as ai_task:
            action = ai_task.input_data.get("action", "")
            content = ai_task.input_data.get("content", "")
            language = ai_task.input_data.get("language", "")
            filename = ai_task.input_data.get("filename", "")

            builders = {
                "improve": lambda: build_improve_messages(content, language, filename),
                "explain": lambda: build_explain_messages(
                    content, language, filename, user=ai_task.owner
                ),
                "summarize": lambda: build_summarize_messages(
                    content, language, filename, user=ai_task.owner
                ),
                "custom": lambda: build_custom_messages(
                    content,
                    ai_task.input_data.get("instructions", ""),
                    language,
                    filename,
                    user=ai_task.owner,
                ),
            }

            builder = builders.get(action)
            if not builder:
                ai_task.status = AITask.Status.FAILED
                ai_task.error = gettext("Unknown action: %(action)s") % {
                    "action": action
                }
                return {"status": "error", "error": f"Unknown action: {action}"}

            messages = builder()
            result = run_prompt(messages)
            record_prompt_run(ai_task, result)

            logger.info(
                "Editor action complete: task=%s action=%s tokens=%s+%s",
                scrub(task_id),
                scrub(action),
                result.usage.input_tokens,
                result.usage.output_tokens,
            )
            return {"status": "ok", "task_id": task_id}
    except TaskNotPending:
        return {"status": "skipped", "reason": "not_pending"}
    except AITask.DoesNotExist:
        logger.error("Editor action task not found: %s", scrub(task_id))
        return {"status": "error", "error": "Task not found"}
    except Exception:
        # ai_task_lifecycle has already marked the AITask row FAILED, fired
        # notify_sse, and called logger.exception(). Re-raise so Celery's
        # result backend records the task as FAILED too - otherwise
        # ``celery_tasks_total{state='success'}`` would tick on a failed
        # task and Flower / monitoring would miss it. ``max_retries=0`` so
        # this does not actually retry.
        raise
