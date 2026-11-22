import logging
import time
from datetime import datetime, timedelta

from django.db.models import Prefetch
from django.utils import timezone

from workspace.core.sse_registry import SSEProvider

from .models import (
    Conversation,
    ConversationMember,
    Message,
    MessageInteraction,
    MessageLinkPreview,
    PinnedMessage,
    Reaction,
)
from .serializers import MessageInteractionSerializer, MessageSerializer
from .services.conversations import get_unread_counts, user_conversation_ids

logger = logging.getLogger(__name__)

# A row is stamped before its transaction commits, so a poll looks this far
# back from where it stands.
_IN_FLIGHT = timedelta(seconds=5)
# How far before the last look of the stream it follows a resumed stream looks
# for messages at most: with none sent for long, a conversation joined since
# would otherwise hand over its history as new messages.
_RESUME_REACH = timedelta(minutes=1)
# Messages sent by one poll at most, oldest first.
_MESSAGE_BATCH = 50


def _parse_resume_point(raw):
    """``(since, polled_at)`` out of a resume point, None out of anything else."""
    try:
        since, polled_at = (datetime.fromisoformat(part) for part in raw.split("|"))
    except AttributeError, TypeError, ValueError:
        return None
    if since.tzinfo is None or polled_at.tzinfo is None:
        return None
    return since, polled_at


class ChatSSEProvider(SSEProvider):
    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)

        self._member_conv_ids = set(user_conversation_ids(user))

        # Where the stream this one follows stood (resume_point). Anything
        # else, a malformed id included, starts from now. Neither date is
        # taken ahead of the clock: nothing written until then would be sent.
        now = timezone.now()
        since, polled_at = _parse_resume_point(last_event_id) or (now, now)
        # When the database was last looked at and held nothing more to send.
        self._polled_at = min(polled_at, now)
        self._resumed_at = self._polled_at
        # Messages are sent from the last one sent on.
        self._since = min(max(since, self._polled_at - _RESUME_REACH), now)

        self._conv_titles = self._current_titles()
        self._seen_message_ids = set()
        self._seen_edit_keys = set()
        self._seen_delete_keys = set()
        self._seen_reaction_ids = set()
        self._seen_pin_ids = set()
        self._seen_link_preview_ids = set()
        self._seen_interaction_keys = set()
        self._last_unread_push = 0
        self._last_read_check = self._polled_at
        self._last_typing_state = {}

    def resume_point(self):
        return f"{self._since.isoformat()}|{self._polled_at.isoformat()}"

    def _current_titles(self):
        return dict(
            Conversation.objects.filter(uuid__in=self._member_conv_ids).values_list(
                "uuid", "title"
            )
        )

    def get_initial_events(self):
        # A failure is the caller's to log: it also has to say that its answer
        # is short of these counts.
        unread = get_unread_counts(self.user)
        self._last_unread_push = time.time()
        return [("unread", unread, None)]

    def poll(self, cache_value):
        events = []
        user_id = self.user.id

        # Typing — always check (independent of dirty flag)
        from .services.typing import get_typing_users

        typing_data = get_typing_users(self._member_conv_ids, exclude_user_id=user_id)
        if typing_data != self._last_typing_state:
            self._last_typing_state = typing_data
            events.append(("typing", typing_data, None))

        # Call events (lifecycle + WebRTC signaling) - always drained, like
        # typing, so signaling latency stays low even on idle (None) polls.
        from .services.call_signaling import drain_events

        for envelope in drain_events(user_id):
            events.append((envelope["event"], envelope["data"], None))

        # Only query for new events if dirty flag changed
        if cache_value is None:
            return events

        # Taken before the queries: what commits while they run is looked for
        # again by whoever resumes from here.
        looked_at = timezone.now()

        # Push updated unread counts (at most every 5 seconds)
        now = time.time()
        if now - self._last_unread_push >= 5:
            try:
                unread = get_unread_counts(self.user)
                events.append(("unread", unread, None))
            except Exception:
                logger.exception(
                    "Failed to send unread counts for user %s",
                    user_id,
                )
            self._last_unread_push = now

        # Refresh member conversation IDs
        self._member_conv_ids = set(user_conversation_ids(self.user))

        # Conversation title changes (AI regeneration, renames by other
        # members). Conversations entering the snapshot (new membership) don't
        # emit - the first message event already triggers a sidebar refresh.
        current_titles = self._current_titles()
        for conv_id, title in current_titles.items():
            if conv_id in self._conv_titles and self._conv_titles[conv_id] != title:
                data = {
                    "type": "conversation_updated",
                    "conversation_id": str(conv_id),
                    "title": title,
                }
                events.append(("conversation_updated", data, None))
        self._conv_titles = current_titles

        # New messages
        new_messages = list(
            Message.objects.filter(
                conversation_id__in=self._member_conv_ids,
                created_at__gt=self._since,
                deleted_at__isnull=True,
            )
            .exclude(author_id=user_id)
            .exclude(uuid__in=self._seen_message_ids)
            .select_related(
                "author",
                "author__bot_profile",
                "reply_to",
                "reply_to__author",
                "interaction",
                "interaction__interacted_by",
            )
            .prefetch_related(
                Prefetch(
                    "reactions",
                    queryset=Reaction.objects.select_related("user"),
                ),
                "attachments",
                "link_previews__preview",
            )
            .order_by("created_at")[:_MESSAGE_BATCH]
        )
        if len(new_messages) < _MESSAGE_BATCH:
            # A full batch leaves messages behind, and a stream resumed from
            # this look would step over them.
            self._polled_at = looked_at
        for msg in new_messages:
            self._seen_message_ids.add(msg.uuid)
            data = {
                "type": "message",
                "conversation_id": str(msg.conversation_id),
                "message": MessageSerializer(msg).data,
            }
            self._since = max(self._since, msg.created_at)
            # Its id is where the stream stands once it is sent.
            events.append(("message", data, self.resume_point()))

        # Changes are looked for from the last message on, as messages are,
        # but never from before the stream this one follows last looked: it
        # sent those already.
        changes_since = max(self._since, self._resumed_at) - _IN_FLIGHT

        # Edited messages
        edited_messages = (
            Message.objects.filter(
                conversation_id__in=self._member_conv_ids,
                edited_at__isnull=False,
                edited_at__gt=changes_since,
            )
            .exclude(author_id=user_id)
            .select_related("author")
            .order_by("edited_at")[:50]
        )
        for msg in edited_messages:
            edit_key = f"{msg.uuid}:{msg.edited_at.isoformat()}"
            if edit_key in self._seen_edit_keys:
                continue
            self._seen_edit_keys.add(edit_key)
            data = {
                "type": "message_edited",
                "conversation_id": str(msg.conversation_id),
                "message_id": str(msg.uuid),
                "body": msg.body,
                "body_html": msg.body_html,
                "edited_at": msg.edited_at.isoformat(),
            }
            events.append(("message_edited", data, None))

        # Interaction updates (someone answered an ask_user_question prompt)
        updated_interactions = (
            MessageInteraction.objects.filter(
                message__conversation_id__in=self._member_conv_ids,
                interacted_at__isnull=False,
                interacted_at__gt=changes_since,
            )
            .exclude(interacted_by_id=user_id)
            .select_related("message", "interacted_by")
            .order_by("interacted_at")[:50]
        )
        for interaction in updated_interactions:
            key = f"{interaction.uuid}:{interaction.interacted_at.isoformat()}"
            if key in self._seen_interaction_keys:
                continue
            self._seen_interaction_keys.add(key)
            data = {
                "type": "message_interaction_updated",
                "conversation_id": str(interaction.message.conversation_id),
                "message_id": str(interaction.message_id),
                "interaction": MessageInteractionSerializer(interaction).data,
            }
            events.append(("message_interaction_updated", data, None))

        # Deleted messages
        deleted_messages = (
            Message.objects.filter(
                conversation_id__in=self._member_conv_ids,
                deleted_at__isnull=False,
                deleted_at__gt=changes_since,
            )
            .exclude(author_id=user_id)
            .order_by("deleted_at")[:50]
        )
        for msg in deleted_messages:
            del_key = str(msg.uuid)
            if del_key in self._seen_delete_keys:
                continue
            self._seen_delete_keys.add(del_key)
            data = {
                "type": "message_deleted",
                "conversation_id": str(msg.conversation_id),
                "message_id": str(msg.uuid),
            }
            events.append(("message_deleted", data, None))

        # Reactions
        new_reactions = (
            Reaction.objects.filter(
                message__conversation_id__in=self._member_conv_ids,
                created_at__gt=changes_since,
            )
            .exclude(user_id=user_id)
            .exclude(uuid__in=self._seen_reaction_ids)
            .select_related("user", "message")
            .order_by("created_at")[:50]
        )
        for reaction in new_reactions:
            self._seen_reaction_ids.add(reaction.uuid)
            data = {
                "type": "reaction",
                "conversation_id": str(reaction.message.conversation_id),
                "message_id": str(reaction.message.uuid),
                "emoji": reaction.emoji,
                "user": {
                    "id": reaction.user.id,
                    "username": reaction.user.username,
                },
                "action": "added",
            }
            events.append(("reaction", data, None))

        # Pinned messages
        new_pins = (
            PinnedMessage.objects.filter(
                conversation_id__in=self._member_conv_ids,
                created_at__gt=changes_since,
            )
            .exclude(pinned_by_id=user_id)
            .exclude(uuid__in=self._seen_pin_ids)
            .select_related("pinned_by")
            .order_by("created_at")[:50]
        )
        for pin in new_pins:
            self._seen_pin_ids.add(pin.uuid)
            data = {
                "type": "message_pinned",
                "conversation_id": str(pin.conversation_id),
                "message_id": str(pin.message_id),
                "pinned_by": {
                    "id": pin.pinned_by.id,
                    "username": pin.pinned_by.username,
                },
            }
            events.append(("message_pinned", data, None))

        # Link previews
        new_link_previews = (
            MessageLinkPreview.objects.filter(
                message__conversation_id__in=self._member_conv_ids,
                created_at__gt=changes_since,
            )
            .exclude(uuid__in=self._seen_link_preview_ids)
            .values_list("message__conversation_id", flat=True)
            .distinct()[:50]
        )
        lp_conv_ids = set(new_link_previews)
        if lp_conv_ids:
            recent_lp_ids = set(
                MessageLinkPreview.objects.filter(
                    message__conversation_id__in=lp_conv_ids,
                    created_at__gt=changes_since,
                ).values_list("uuid", flat=True)
            )
            self._seen_link_preview_ids |= recent_lp_ids
            for conv_id in lp_conv_ids:
                data = {
                    "type": "link_preview",
                    "conversation_id": str(conv_id),
                }
                events.append(("link_preview", data, None))

        # Read receipts: detect members who read conversations since last poll
        recent_reads = (
            ConversationMember.objects.filter(
                conversation_id__in=self._member_conv_ids,
                last_read_at__gt=self._last_read_check,
                left_at__isnull=True,
            )
            .exclude(user_id=user_id)
            .values_list("conversation_id", flat=True)
            .distinct()
        )
        read_conv_ids = set(recent_reads)
        if read_conv_ids:
            self._last_read_check = timezone.now()
            for conv_id in read_conv_ids:
                data = {
                    "type": "read",
                    "conversation_id": str(conv_id),
                }
                events.append(("read", data, None))

        return events
