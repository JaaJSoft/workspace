import json
import logging

from .models import UserMemory

logger = logging.getLogger(__name__)

CHAT_TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'save_memory',
            'description': (
                'Save or update a fact about the user for future conversations. '
                'Use this when the user shares personal information, preferences, '
                'or context you should remember.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'key': {
                        'type': 'string',
                        'description': 'A short category label (e.g. name, language, project, preference).',
                    },
                    'content': {
                        'type': 'string',
                        'description': 'The fact to remember.',
                    },
                },
                'required': ['key', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'delete_memory',
            'description': (
                'Delete a previously saved memory about the user. '
                'Use this when the user asks you to forget something.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'key': {
                        'type': 'string',
                        'description': 'The key of the memory to delete.',
                    },
                },
                'required': ['key'],
            },
        },
    },
]


def execute_tool_call(tool_call, user, bot) -> str:
    """Execute a tool call and return the result string."""
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        return 'Error: invalid JSON arguments'

    if name == 'save_memory':
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

    elif name == 'delete_memory':
        key = args.get('key', '').strip()
        deleted, _ = UserMemory.objects.filter(user=user, bot=bot, key=key).delete()
        if deleted:
            logger.info('Memory deleted: %s/%s — %s', user.username, bot.username, key)
            return f'Deleted memory "{key}".'
        return f'Memory "{key}" not found.'

    return f'Unknown tool: {name}'
