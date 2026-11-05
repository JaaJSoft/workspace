"""The AITask a dispatched bot reply leaves for its worker."""

from workspace.ai.models import AITask


def queue_chat_task(conversation, message, bot_user):
    """Create the PENDING row ``_trigger_bot_response`` dispatches; return its id."""
    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        input_data={
            "conversation_id": str(conversation.pk),
            "message_id": str(message.uuid),
        },
    )
    return str(ai_task.uuid)
