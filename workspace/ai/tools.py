"""Core AI chat tools (memory, workspace search, avatar, image generation)."""
import base64
import io
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
        """Get the profile of the user you are chatting with: username, full name, email, and join date. \
Call this when you need to address the user by name, check their email, or answer questions about their account."""
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
        """Persistently save a fact about the user so you can recall it in future conversations. \
Call this proactively when the user tells you their name, preferences, projects, or any personal detail worth remembering. \
If the key already exists it will be updated."""
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
        """Delete a previously saved memory. \
Call this when the user explicitly asks you to forget something or when a stored fact is no longer correct."""
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
        """Search across the entire workspace — files, conversations, emails, calendar events, and contacts — \
for items matching a keyword. Returns results from all modules at once. \
Call this when the user asks to find, look up, or locate anything. Use a single keyword or short phrase for best results. \
You can then use read_email or read_file with the returned UUIDs to get full content."""
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
        """Retrieve your own avatar image so you can see or describe it. \
Call this when the user asks what you look like, wants to see your avatar, or mentions your appearance."""
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
        """Generate a brand-new image from a text description. \
Call this when the user asks you to create, draw, generate, or make an image from scratch. \
Do NOT use this to modify an existing image — use edit_image instead."""
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

    def _edit_via_openai(self, client, image_file, prompt, size):
        """Try editing via the OpenAI-compatible /v1/images/edits endpoint."""
        response = client.images.edit(
            model=settings.AI_IMAGE_MODEL,
            image=image_file,
            prompt=prompt,
            size=size,
            n=1,
            response_format='b64_json',
        )
        return base64.b64decode(response.data[0].b64_json)

    def _edit_via_ollama(self, source_data, prompt):
        """Fallback: use Ollama native /api/generate with images param (img2img)."""
        import httpx
        base_url = (settings.AI_IMAGE_BASE_URL or settings.AI_BASE_URL or '').rstrip('/')
        if base_url.endswith('/v1'):
            base_url = base_url[:-3]
        resp = httpx.post(
            f'{base_url}/api/generate',
            json={
                'model': settings.AI_IMAGE_MODEL,
                'prompt': prompt,
                'images': [base64.b64encode(source_data).decode()],
                'stream': False,
            },
            timeout=settings.AI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns 'image' (singular) for img2img
        result_b64 = data.get('image') or ''
        if not result_b64:
            raise RuntimeError(f'no image returned from Ollama — response keys: {list(data.keys())}')
        return base64.b64decode(result_b64)

    @tool(badge_icon='✏️', badge_label='Edited image', detail_key='prompt', params={
        'prompt': Param('A description of the changes to apply to the image.'),
        'size': Param('Output size: 1024x1024, 1792x1024, or 1024x1792.', required=False),
    })
    def edit_image(self, args, user, bot, conversation_id, context):
        """Edit an existing image from the conversation based on a text instruction. \
Automatically uses the most recent image in the conversation as the source. \
Call this when the user asks you to modify, change, update, transform, or edit a picture — \
for example "make it darker", "remove the background", "add a hat". \
Do NOT use this to create an image from scratch — use generate_image instead."""
        prompt = args.get('prompt', '').strip()
        if not prompt:
            return 'Error: prompt is required'
        if not conversation_id:
            return 'Error: no conversation context'

        client = get_image_client()
        if not client:
            return 'Error: AI is not configured'

        # Find the most recent image attachment in the conversation
        from workspace.chat.models import MessageAttachment
        attachment = (
            MessageAttachment.objects.filter(
                message__conversation_id=conversation_id,
                mime_type__startswith='image/',
            )
            .order_by('-message__created_at', '-created_at')
            .first()
        )
        if not attachment:
            return 'Error: no image found in the conversation to edit'

        try:
            source_data = attachment.file.read()
        except Exception:
            logger.warning('Could not read attachment %s for editing', attachment.uuid)
            return 'Error: could not read the source image'

        size = args.get('size', '1024x1024')
        if size not in ('1024x1024', '1792x1024', '1024x1792'):
            size = '1024x1024'

        logger.info(
            'Starting image edit: model=%s size=%s source=%s prompt=%.80s',
            settings.AI_IMAGE_MODEL, size, attachment.uuid, prompt,
        )

        # Try OpenAI-compatible endpoint first, fall back to Ollama native API
        try:
            image_file = io.BytesIO(source_data)
            image_file.name = attachment.original_name or 'image.png'
            image_data = self._edit_via_openai(client, image_file, prompt, size)
            logger.info('Image edited via OpenAI endpoint: model=%s bytes=%d', settings.AI_IMAGE_MODEL, len(image_data))
        except Exception as openai_err:
            logger.info('OpenAI images.edit failed (%s), falling back to Ollama native API', openai_err)
            try:
                image_data = self._edit_via_ollama(source_data, prompt)
                logger.info('Image edited via Ollama native API: model=%s bytes=%d', settings.AI_IMAGE_MODEL, len(image_data))
            except Exception as ollama_err:
                logger.exception('Image edit failed on both OpenAI and Ollama backends')
                return f'Error: image edit failed — {ollama_err}'

        context.setdefault('images', []).append({
            'data': image_data,
            'prompt': prompt,
            'size': size,
        })

        return f'Image edited successfully: {prompt}'
