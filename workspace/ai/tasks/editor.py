"""Celery wrappers for editor AI actions."""

from celery import shared_task


@shared_task(name='ai.editor_action', bind=True, max_retries=0)
def editor_action(self, task_id: str):
    """Run an AI action on editor content (improve, explain, summarize, custom)."""
    from workspace.ai.services.editor import run_editor_action
    return run_editor_action(task_id)
