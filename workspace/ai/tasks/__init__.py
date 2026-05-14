"""Celery task entrypoints for the AI module.

Each submodule contains thin ``@shared_task`` wrappers for one category
of work; the actual logic lives in ``workspace/ai/services/<feature>.py``.

This file re-exports every task at the package level so that:
- Celery's ``autodiscover_tasks()`` finds them all when it imports
  ``workspace.ai.tasks``.
- External callers and test mock paths
  (``from workspace.ai.tasks import X``,
  ``@patch('workspace.ai.tasks.X.delay')``) keep working unchanged
  after the file -> package conversion.
"""

from workspace.ai.tasks.calendar import extract_from_mail_messages
from workspace.ai.tasks.chat import (
    generate_chat_response,
    generate_conversation_title,
    update_conversation_summary,
)
from workspace.ai.tasks.editor import editor_action
from workspace.ai.tasks.housekeeping import purge_ai_tasks
from workspace.ai.tasks.mail import (
    classify_mail_messages,
    compose_email,
    summarize,
)
from workspace.ai.tasks.scheduled import (
    dispatch_scheduled_messages,
    generate_scheduled_response,
)

__all__ = [
    'classify_mail_messages',
    'compose_email',
    'dispatch_scheduled_messages',
    'editor_action',
    'extract_from_mail_messages',
    'generate_chat_response',
    'generate_conversation_title',
    'generate_scheduled_response',
    'purge_ai_tasks',
    'summarize',
    'update_conversation_summary',
]
