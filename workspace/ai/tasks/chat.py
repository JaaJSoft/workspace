"""Celery wrappers for chat-related AI tasks."""

from celery import shared_task


@shared_task(name='ai.generate_chat_response', bind=True, max_retries=0)
def generate_chat_response(self, conversation_id: str, message_id: str, bot_user_id: int):
    """Generate a bot response in a chat conversation."""
    from workspace.ai.services.chat_response import generate_response
    return generate_response(conversation_id, message_id, bot_user_id)


@shared_task(name='ai.update_conversation_summary', bind=True, max_retries=0)
def update_conversation_summary(self, conversation_id: str):
    """Update the rolling summary for a bot conversation."""
    from workspace.ai.services.chat_summary import update_summary
    return update_summary(conversation_id)


@shared_task(name='ai.generate_conversation_title', bind=True, max_retries=0)
def generate_conversation_title(self, conversation_id: str):
    """Generate a short title for a bot conversation based on the first exchange."""
    from workspace.ai.services.chat_title import generate_title
    return generate_title(conversation_id)
