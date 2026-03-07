"""AI tools for the Chat module."""
from workspace.ai.tool_registry import Param, ToolProvider, tool


class ChatToolProvider(ToolProvider):

    @tool(badge_icon='🔍', badge_label='Searched', detail_key='query', params={
        'query': Param('The search term to look for in message content.'),
    })
    def search_messages(self, args, user, bot, conversation_id):
        """Search through the current conversation history for messages matching a query. \
Use this when the user asks about something said earlier or wants to find a specific message."""
        query = args.get('query', '').strip()
        if not query:
            return 'Error: query is required'
        if not conversation_id:
            return 'Error: no conversation context'
        from workspace.chat.models import Message
        matches = (
            Message.objects.filter(
                conversation_id=conversation_id,
                deleted_at__isnull=True,
                body__icontains=query,
            )
            .select_related('author')
            .order_by('-created_at')[:10]
        )
        if not matches:
            return f'No messages found matching "{query}".'
        results = []
        for msg in matches:
            author = msg.author.get_full_name() or msg.author.username
            snippet = msg.body[:200]
            ts = msg.created_at.strftime('%Y-%m-%d %H:%M')
            results.append(f'[{ts}] {author}: {snippet}')
        return '\n'.join(results)
