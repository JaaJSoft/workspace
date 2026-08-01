"""Membership sync for group-linked conversations.

Wired in apps.ChatConfig.ready(). The pre_delete handler exists because the
SQL cascade on the M2M through table does not fire m2m_changed.
"""


def _resync_group_conversations(group_ids):
    from .models import Conversation
    from .services.group_sync import resync_conversation_members

    conversations = (
        Conversation.objects.filter(groups__in=group_ids)
        .distinct()
        .prefetch_related("groups")
    )
    for conversation in conversations:
        resync_conversation_members(conversation)


def sync_on_user_groups_changed(sender, instance, action, pk_set, reverse, **kwargs):
    # forward clear: the affected groups are only known before the rows vanish
    if action == "pre_clear" and not reverse:
        instance._chat_groups_before_clear = list(
            instance.groups.values_list("pk", flat=True)
        )
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:
        group_ids = [instance.pk]
    elif action == "post_clear":
        group_ids = getattr(instance, "_chat_groups_before_clear", [])
    else:
        group_ids = list(pk_set or [])
    if group_ids:
        _resync_group_conversations(group_ids)


def handle_group_pre_delete(sender, instance, **kwargs):
    from .services.group_sync import resync_conversation_members

    for conversation in instance.conversations.all():
        if conversation.groups.exclude(pk=instance.pk).exists():
            conversation.groups.remove(instance)
            resync_conversation_members(conversation)
        else:
            conversation.delete()
