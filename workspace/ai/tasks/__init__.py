"""Celery task entrypoints for the AI module.

This package's submodules each contain @shared_task-decorated workers.
Tasks are registered with Celery in workspace/ai/apps.py:ready() by
importing each submodule (which triggers the decorator). Callers MUST
import the task from its specific submodule:

    from workspace.ai.tasks.mail import classify_mail_messages
    from workspace.ai.tasks.chat import generate_chat_response

NOT from this __init__.py (which would be a re-export forbidden by
the project's "never re-export" policy in CLAUDE.md).
"""
