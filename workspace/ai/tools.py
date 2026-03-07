"""Core AI chat tools (memory, workspace search, avatar, image generation)."""
import base64
import json
import logging

from django.conf import settings

from .client import get_image_client
from .models import UserMemory
from .tool_registry import Param, ToolProvider, tool

logger = logging.getLogger(__name__)


class CoreToolProvider(ToolProvider):

    @tool(badge_icon='👤', badge_label='Looked up profile')
    def get_current_user_info(self, args, user, bot, conversation_id, context):
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
    def save_memory(self, args, user, bot, conversation_id, context):
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
    def delete_memory(self, args, user, bot, conversation_id, context):
        """Delete a previously saved memory about the user. \
Use this when the user asks you to forget something."""
        key = args.get('key', '').strip()
        deleted, _ = UserMemory.objects.filter(user=user, bot=bot, key=key).delete()
        if deleted:
            logger.info('Memory deleted: %s/%s — %s', user.username, bot.username, key)
            return f'Deleted memory "{key}".'
        return f'Memory "{key}" not found.'

    @tool(badge_icon='🔎', badge_label='Searched workspace', detail_key='query', params={
        'query': Param("The search term to find across all workspace modules. it's a 'like %term%' in the database. so one term for each search"),
    })
    def search_workspace(self, args, user, bot, conversation_id, context):
        """Search across the entire workspace (files, conversations, emails, calendar events, contacts) \
for items matching a query. Use this when the user asks to find, look up, or locate something in their workspace."""
        query = args.get('query', '').strip()
        if not query:
            return 'Error: query is required'
        from workspace.core.module_registry import registry
        results = registry.search(query, user, limit=50)
        if not results:
            return f'No results found for "{query}" across the workspace.'
        return json.dumps(results, ensure_ascii=False)

    @tool(badge_icon='🖼️', badge_label='Viewed own avatar')
    def get_my_avatar(self, args, user, bot, conversation_id, context):
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



class ImageToolProvider(ToolProvider):
    """Registered only when AI_IMAGE_MODEL is configured."""

    @tool(badge_icon='🎨', badge_label='Generated image', detail_key='prompt', params={
        'prompt': Param('A detailed description of the image to generate.'),
        'size': Param('Image size: 1024x1024, 1792x1024, or 1024x1792.', required=False),
    })
    def generate_image(self, args, user, bot, conversation_id, context):
        """Generate an image from a text description. \
Use this when the user asks you to create, draw, generate, or make an image or picture."""
        prompt = args.get('prompt', '').strip()
        if not prompt:
            return 'Error: prompt is required'
        if not conversation_id:
            return 'Error: no conversation context'

        client = get_image_client()
        if not client:
            return 'Error: AI is not configured'

        size = args.get('size', '1024x1024')
        if size not in ('1024x1024', '1792x1024', '1024x1792'):
            size = '1024x1024'
        logger.info(
            'Starting image generation: model=%s size=%s prompt=%.80s',
            settings.AI_IMAGE_MODEL, size, prompt,
        )
        try:
            response = client.images.generate(
                model=settings.AI_IMAGE_MODEL,
                prompt=prompt,
                size=size,
                n=1,
                response_format='b64_json',
            )
        except Exception as e:
            logger.exception('Image generation failed')
            return f'Error: image generation failed — {e}'

        image_data = base64.b64decode(response.data[0].b64_json)
        logger.info(
            'Image generated: model=%s size=%s bytes=%d prompt=%.80s',
            settings.AI_IMAGE_MODEL, size, len(image_data), prompt,
        )

        context.setdefault('images', []).append({
            'data': image_data,
            'prompt': prompt,
            'size': size,
        })

        return f'Image generated successfully for: {prompt}'
