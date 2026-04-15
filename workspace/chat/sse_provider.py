import time
import logging
from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from workspace.core.sse_registry import SSEProvider

from .models import ConversationMember, Message, MessageLinkPreview, PinnedMessage, Reaction
from .serializers import MessageSerializer
from .services.conversations import get_unread_counts, user_conversation_ids

logger = logging.getLogger(__name__)


class ChatSSEProvider(SSEProvider):
    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)
        user_id = user.id

        # Get conversations user is a member of
        self._member_conv_ids = set(user_conversation_ids(user))

        # Determine "since" timestamp
        if last_event_id:
            try:
                msg = Message.objects.get(uuid=last_event_id)
                self._since = msg.created_at
            except Message.DoesNotExist:
                self._since = timezone.now()
        else:
            self._since = timezone.now()

        self._seen_message_ids = set()
        self._seen_edit_keys = set()
        self._seen_delete_keys = set()
        self._seen_reaction_ids = set()
        self._seen_pin_ids = set()
        self._seen_link_preview_ids = set()
        self._last_unread_push = 0
        self._last_read_check = timezone.now()
        self._last_typing_state = {}

    def get_initial_events(self):
        events = []
        try:
            unread = get_unread_counts(self.user)
            events.append(('unread', unread, None))
            self._last_unread_push = time.time()
        except Exception:
            logger.exception(
                "Failed to send initial unread counts for user %s", self.user.id,
            )
        return events

    def poll(self, cache_value):
        events = []
        user_id = self.user.id

        # Typing — always check (independent of dirty flag)
        from .services.typing import get_typing_users
        typing_data = get_typing_users(self._member_conv_ids, exclude_user_id=user_id)
        if typing_data != self._last_typing_state:
            self._last_typing_state = typing_data
            events.append(('typing', typing_data, None))

        # Only query for new events if dirty flag changed
        if cache_value is None:
            return events

        # Push updated unread counts (at most every 5 seconds)
        now = time.time()
        if now - self._last_unread_push >= 5:
            try:
                unread = get_unread_counts(self.user)
                events.append(('unread', unread, None))
            except Exception:
                logger.exception(
                    "Failed to send unread counts for user %s", user_id,
                )
            self._last_unread_push = now

        # Refresh member conversation IDs
        self._member_conv_ids = set(user_conversation_ids(self.user))

        # New messages
        new_messages = list(
            Message.objects.filter(
                conversation_id__in=self._member_conv_ids,
                created_at__gt=self._since,
                deleted_at__isnull=True,
            )
            .exclude(author_id=user_id)
            .exclude(uuid__in=self._seen_message_ids)
            .select_related('author', 'author__bot_profile', 'reply_to', 'reply_to__author')
            .prefetch_related(
                Prefetch(
                    'reactions',
                    queryset=Reaction.objects.select_related('user'),
                ),
                'attachments',
                'link_previews__preview',
            )
            .order_by('created_at')[:50]
        )
        for msg in new_messages:
            self._seen_message_ids.add(msg.uuid)
            data = {
                'type': 'message',
                'conversation_id': str(msg.conversation_id),
                'message': MessageSerializer(msg).data,
            }
            events.append(('message', data, str(msg.uuid)))
            self._since = max(self._since, msg.created_at)

        # Edited messages
        edited_messages = (
            Message.objects.filter(
                conversation_id__in=self._member_conv_ids,
                edited_at__isnull=False,
                edited_at__gt=self._since - timedelta(seconds=5),
            )
            .exclude(author_id=user_id)
            .select_related('author')
            .order_by('edited_at')[:50]
        )
        for msg in edited_messages:
            edit_key = f'{msg.uuid}:{msg.edited_at.isoformat()}'
            if edit_key in self._seen_edit_keys:
                continue
            self._seen_edit_keys.add(edit_key)
            data = {
                'type': 'message_edited',
                'conversation_id': str(msg.conversation_id),
                'message_id': str(msg.uuid),
                'body': msg.body,
                'body_html': msg.body_html,
                'edited_at': msg.edited_at.isoformat(),
            }
            events.append(('message_edited', data, None))

        # Deleted messages
        deleted_messages = (
            Message.objects.filter(
                conversation_id__in=self._member_conv_ids,
                deleted_at__isnull=False,
                deleted_at__gt=self._since - timedelta(seconds=5),
            )
            .exclude(author_id=user_id)
            .order_by('deleted_at')[:50]
        )
        for msg in deleted_messages:
            del_key = str(msg.uuid)
            if del_key in self._seen_delete_keys:
                continue
            self._seen_delete_keys.add(del_key)
            data = {
                'type': 'message_deleted',
                'conversation_id': str(msg.conversation_id),
                'message_id': str(msg.uuid),
            }
            events.append(('message_deleted', data, None))

        # Reactions
        new_reactions = (
            Reaction.objects.filter(
                message__conversation_id__in=self._member_conv_ids,
                created_at__gt=self._since - timedelta(seconds=5),
            )
            .exclude(user_id=user_id)
            .exclude(uuid__in=self._seen_reaction_ids)
            .select_related('user', 'message')
            .order_by('created_at')[:50]
        )
        for reaction in new_reactions:
            self._seen_reaction_ids.add(reaction.uuid)
            data = {
                'type': 'reaction',
                'conversation_id': str(reaction.message.conversation_id),
                'message_id': str(reaction.message.uuid),
                'emoji': reaction.emoji,
                'user': {
                    'id': reaction.user.id,
                    'username': reaction.user.username,
                },
                'action': 'added',
            }
            events.append(('reaction', data, None))

        # Pinned messages
        new_pins = (
            PinnedMessage.objects.filter(
                conversation_id__in=self._member_conv_ids,
                created_at__gt=self._since - timedelta(seconds=5),
            )
            .exclude(pinned_by_id=user_id)
            .exclude(uuid__in=self._seen_pin_ids)
            .select_related('pinned_by')
            .order_by('created_at')[:50]
        )
        for pin in new_pins:
            self._seen_pin_ids.add(pin.uuid)
            data = {
                'type': 'message_pinned',
                'conversation_id': str(pin.conversation_id),
                'message_id': str(pin.message_id),
                'pinned_by': {
                    'id': pin.pinned_by.id,
                    'username': pin.pinned_by.username,
                },
            }
            events.append(('message_pinned', data, None))

        # Link previews
        new_link_previews = (
            MessageLinkPreview.objects.filter(
                message__conversation_id__in=self._member_conv_ids,
                created_at__gt=self._since - timedelta(seconds=5),
            )
            .exclude(uuid__in=self._seen_link_preview_ids)
            .values_list('message__conversation_id', flat=True)
            .distinct()[:50]
        )
        lp_conv_ids = set(new_link_previews)
        if lp_conv_ids:
            recent_lp_ids = set(
                MessageLinkPreview.objects.filter(
                    message__conversation_id__in=lp_conv_ids,
                    created_at__gt=self._since - timedelta(seconds=5),
                ).values_list('uuid', flat=True)
            )
            self._seen_link_preview_ids |= recent_lp_ids
            for conv_id in lp_conv_ids:
                data = {
                    'type': 'link_preview',
                    'conversation_id': str(conv_id),
                }
                events.append(('link_preview', data, None))

        # Read receipts: detect members who read conversations since last poll
        recent_reads = (
            ConversationMember.objects.filter(
                conversation_id__in=self._member_conv_ids,
                last_read_at__gt=self._last_read_check,
                left_at__isnull=True,
            )
            .exclude(user_id=user_id)
            .values_list('conversation_id', flat=True)
            .distinct()
        )
        read_conv_ids = set(recent_reads)
        if read_conv_ids:
            self._last_read_check = timezone.now()
            for conv_id in read_conv_ids:
                data = {
                    'type': 'read',
                    'conversation_id': str(conv_id),
                }
                events.append(('read', data, None))

        return events
