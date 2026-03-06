"""Core AI chat tools (memory, search, avatar)."""
import base64
import json
import logging

from .models import UserMemory
from .tool_registry import Param, ToolProvider, tool

logger = logging.getLogger(__name__)


class CoreToolProvider(ToolProvider):

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

    @tool(badge_icon='👤', badge_label='Looked up profile')
    def get_current_user_info(self, args, user, bot, conversation_id):
        """Get profile information about the current user you are talking to. \
Use this when you need to know the user's name, email, or other profile details."""
        if not user:
            return 'Error: no user context'
        info = {
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'date_joined': user.date_joined.strftime('%Y-%m-%d'),
        }
        return json.dumps(info)

    @tool(badge_icon='🧠', badge_label='Retained', detail_key='key', params={
        'key': Param('A short category label (e.g. name, language, project, preference).'),
        'content': Param('The fact to remember.'),
    })
    def save_memory(self, args, user, bot, conversation_id):
        """Save or update a fact about the user for future conversations. \
Use this when the user shares personal information, preferences, or context you should remember."""
        key = args.get('key', '').strip()[:100]
        content = args.get('content', '').strip()
        if not key or not content:
            return 'Error: key and content are required'
        UserMemory.objects.update_or_create(
            user=user, bot=bot, key=key,
            defaults={'content': content},
        )
        logger.info('Memory saved: %s/%s — %s', user.username, bot.username, key)
        return f'Saved memory "{key}".'

    @tool(badge_icon='🧠', badge_label='Forgot', detail_key='key', params={
        'key': Param('The key of the memory to delete.'),
    })
    def delete_memory(self, args, user, bot, conversation_id):
        """Delete a previously saved memory about the user. \
Use this when the user asks you to forget something."""
        key = args.get('key', '').strip()
        deleted, _ = UserMemory.objects.filter(user=user, bot=bot, key=key).delete()
        if deleted:
            logger.info('Memory deleted: %s/%s — %s', user.username, bot.username, key)
            return f'Deleted memory "{key}".'
        return f'Memory "{key}" not found.'

    @tool(badge_icon='🖼️', badge_label='Viewed own avatar')
    def get_my_avatar(self, args, user, bot, conversation_id):
        """Retrieve your own avatar image. \
Use this when the user asks what you look like, about your avatar, or your appearance."""
        if not bot:
            return 'Error: no bot context'
        from django.core.files.storage import default_storage
        from workspace.users.avatar_service import get_avatar_path, has_avatar
        if not has_avatar(bot):
            return 'You do not have an avatar set.'
        try:
            path = get_avatar_path(bot.id)
            with default_storage.open(path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            return json.dumps({
                'type': 'image',
                'mime_type': 'image/webp',
                'data': b64,
            })
        except Exception:
            logger.warning('Could not read avatar for bot %s', bot.id)
            return 'Error: could not read avatar file.'
