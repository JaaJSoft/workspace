"""Whether a bot may still act for a user in a conversation.

A bot run reads and writes the private data of the user it acts for (mail,
files, calendar) and posts the result into the conversation. Access is
checked when a conversation is created or a schedule is set up, but a run
can start long after that - a scheduled message months later, a chat
response queued before the user lost access - so every run re-checks it
when it starts.
"""

from workspace.chat.services.conversations import (
    bot_may_run_in,
    get_active_membership,
)


def run_refusal(bot_profile, user, conversation_id) -> str | None:
    """Why *bot_profile* must not run for *user* in *conversation_id*, or None.

    The reason is a short slug for logs and task results, never shown in
    the conversation.
    """
    if user is None:
        return "no_user"
    if not user.is_active:
        return "user_inactive"
    if get_active_membership(user, conversation_id) is None:
        return "not_a_member"
    if not bot_may_run_in(conversation_id):
        return "not_a_direct_conversation"
    if not bot_profile.is_accessible_by(user):
        return "bot_not_accessible"
    return None
