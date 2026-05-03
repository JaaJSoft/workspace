"""Celery wrappers for mail-related AI tasks."""

from celery import shared_task


@shared_task(name='ai.summarize', bind=True, max_retries=0)
def summarize(self, task_id: str):
    """Summarize a mail message."""
    from workspace.ai.services.mail_summary import summarize_mail
    return summarize_mail(task_id)


@shared_task(name='ai.compose_email', bind=True, max_retries=0)
def compose_email(self, task_id: str):
    """Compose or reply to an email."""
    from workspace.ai.services.mail_compose import compose_mail
    return compose_mail(task_id)


@shared_task(name='ai.classify_mail', bind=True, max_retries=0)
def classify_mail_messages(self, task_id: str):
    """Classify mail messages by assigning user-defined labels."""
    from workspace.ai.services.mail_classifier import classify_mail
    return classify_mail(task_id)
