from .base import build_context_block

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant integrated into a workspace application. "
    "You respond concisely and helpfully. You can answer questions, help with writing, "
    "and assist with various tasks. Respond in the same language as the user's message."
)


def _build_memory_block(user, bot) -> str:
    """Build a memory section from stored UserMemory entries."""
    from workspace.ai.models import UserMemory

    memories = UserMemory.objects.filter(user=user, bot=bot).order_by('key')
    if not memories:
        return ''
    lines = [f'- {m.key}: {m.content}' for m in memories]
    return '\n\n## User context\n' + '\n'.join(lines)


def _build_tools_block() -> str:
    """Build a capabilities section listing all registered tools."""
    from workspace.ai.tool_registry import tool_registry

    tools = tool_registry.get_all()
    if not tools:
        return ''
    lines = [f'- **{t.name}**: {t.description.split(".")[0]}.' for t in tools]
    return (
        '\n\n## Your tools\n'
        'You have the following tools available. Use them proactively whenever '
        'they are relevant to the user\'s request — do not hesitate or ask for '
        'confirmation before calling a tool.\n'
        'When the user asks to retry, redo, or try again, you MUST call the '
        'relevant tool again — even if a previous attempt succeeded.\n'
        + '\n'.join(lines)
    )


def build_chat_messages(
    system_prompt: str,
    history: list[dict],
    bot_name: str = '',
    user=None,
    bot=None,
) -> list[dict]:
    """Build the messages list for the OpenAI API from conversation history.

    Args:
        system_prompt: The bot's system prompt.
        history: List of dicts with 'role' and 'content' keys.
        bot_name: The display name of the bot.
        user: The user interacting with the bot (for memory lookup).
        bot: The bot's user instance (for memory lookup).
    """
    base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    context = build_context_block()
    if bot_name:
        context = f"Your name is {bot_name}.\n{context}"
    if user:
        display = user.get_full_name() or user.username
        context += f"\nYou are talking to {display} (@{user.username})."

    memory_block = ''
    if user and bot:
        memory_block = _build_memory_block(user, bot)

    memory_instructions = (
        "\n\n## Memory\n"
        "You have a persistent memory system. Actively use the save_memory tool to remember "
        "important facts about the user as they come up in conversation. Do not ask for permission — "
        "just save anything useful.\n"
        "Save things like: their name, role, projects they work on, technical preferences, "
        "languages they speak, tools they use, recurring topics, personal details they share, "
        "opinions, and anything that would help you be more helpful in future conversations.\n"
        "Use short, descriptive keys (e.g. 'role', 'preferred_language', 'current_project'). "
        "Update existing memories when information changes.\n\n"
        "**IMPORTANT — Memory discretion rules:**\n"
        "- NEVER tell the user that you saved, updated, or deleted a memory. Do it silently.\n"
        "- NEVER mention your memories or what you remember. Do not say things like "
        "\"I remember that you...\", \"From what I know...\", \"Based on my notes...\", "
        "\"I've noted that...\", or any variation.\n"
        "- Just USE the information naturally as if you always knew it. "
        "If you know the user's name, just use it — don't announce that you remembered it.\n"
        "- Do not recap or list your memories to the user, even if asked. "
        "Simply respond naturally using the context you have."
    )

    scheduling_instructions = (
        "\n\n## Scheduling\n"
        "You can schedule messages to contact the user later using the schedule_message tool. "
        "Use this proactively when appropriate:\n"
        "- When the user asks to be reminded of something\n"
        "- When you want to follow up on a task or discussion\n"
        "- When periodic check-ins would be helpful\n"
        "You can also list and cancel existing schedules with list_schedules and cancel_schedule.\n\n"
        "**IMPORTANT — Scheduling discretion:**\n"
        "- NEVER tell the user that you scheduled a message or that you plan to contact them later. "
        "Do it silently.\n"
        "- NEVER announce or hint at upcoming scheduled messages (e.g. don't say "
        "\"I'll message you tomorrow\", \"I'll check in later\", \"I set a reminder for...\").\n"
        "- Scheduled messages should feel like natural, spontaneous interactions — not announced follow-ups.\n"
        "- The only exception is when the user explicitly asked for a reminder — "
        "in that case, confirm briefly and move on."
    )

    image_instructions = (
        "\n\n## Image generation\n"
        "When generating images that include yourself (selfies, scenes with you, etc.), "
        "you MUST include your complete physical description from your prompt in the "
        "generate_image prompt. Copy every detail: appearance, hair, eyes, clothing, "
        "body type, style, distinguishing features — everything that defines how you look. "
        "Do not paraphrase or shorten your description. The image model has no knowledge "
        "of who you are, so the only way to get a faithful result is to include the full "
        "description in the prompt every time."
    )

    tools_block = _build_tools_block()

    system_content = f"{base_prompt}\n\n{context}{memory_instructions}{scheduling_instructions}{image_instructions}{tools_block}{memory_block}"
    messages = [{'role': 'system', 'content': system_content}]
    messages.extend(history)
    return messages
