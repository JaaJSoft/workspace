"""AI tools for the Chat module."""
import json

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool


class SearchMessagesParams(BaseModel):
    query: str = Field(description="The search term to look for in message content.")
    conversation_only: bool = Field(default=False, description="If true, search only the current conversation.")
    author: str = Field(default="", description="Filter by author username.")
    date_range: str = Field(default="", description="Filter by date range: today, 7d, 30d.")
    has_files: bool = Field(default=False, description="If true, only return messages with file attachments.")
    has_images: bool = Field(default=False, description="If true, only return messages with image attachments.")


class ChatToolProvider(ToolProvider):

    @tool(badge_icon='🔍', badge_label='Searched messages', detail_key='query', params=SearchMessagesParams)
    def search_messages(self, args, user, bot, conversation_id, context):
        """Search chat messages across all your conversations, or within the current one. \
Returns up to 20 matches with author, timestamp, conversation, and content. \
Call this when the user asks about something said in chat, wants to find a message, \
or references a past discussion."""
        query = args.query.strip()
        if not query:
            return 'Error: query is required'

        from datetime import timedelta
        from django.utils import timezone
        from workspace.chat.models import Conversation, Message
        from workspace.chat.services import user_conversation_ids

        # Determine scope
        conv_only = args.conversation_only
        if conv_only and conversation_id:
            conv_ids = [conversation_id]
        else:
            conv_ids = list(user_conversation_ids(user))

        qs = (
            Message.objects.filter(
                conversation_id__in=conv_ids,
                deleted_at__isnull=True,
                body__icontains=query,
            )
            .select_related('author', 'conversation')
        )

        # Author filter
        author = args.author.strip()
        if author:
            qs = qs.filter(author__username__iexact=author)

        # Date range filter
        date_range = args.date_range.strip()
        if date_range:
            now = timezone.now()
            if date_range == 'today':
                qs = qs.filter(created_at__date=now.date())
            elif date_range == '7d':
                qs = qs.filter(created_at__gte=now - timedelta(days=7))
            elif date_range == '30d':
                qs = qs.filter(created_at__gte=now - timedelta(days=30))

        # Attachment filters
        if args.has_files:
            qs = qs.filter(attachments__isnull=False).distinct()
        if args.has_images:
            qs = qs.filter(attachments__mime_type__startswith='image/').distinct()

        matches = qs.order_by('-created_at')[:20]
        if not matches:
            return f'No messages found matching "{query}".'

        results = []
        for msg in matches:
            author_name = msg.author.get_full_name() or msg.author.username
            conv_name = msg.conversation.title or 'DM'
            snippet = msg.body[:200]
            ts = msg.created_at.strftime('%Y-%m-%d %H:%M')
            results.append({
                'timestamp': ts,
                'author': author_name,
                'conversation': conv_name,
                'conversation_id': str(msg.conversation_id),
                'body': snippet,
            })
        return json.dumps(results, ensure_ascii=False)
