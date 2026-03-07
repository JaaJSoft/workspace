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
    return '\n\n## What you remember about this user\n' + '\n'.join(lines)


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
        "Update existing memories when information changes."
    )

    tools_block = _build_tools_block()

    system_content = f"{base_prompt}\n\n{context}{memory_instructions}{tools_block}{memory_block}"
    messages = [{'role': 'system', 'content': system_content}]
    messages.extend(history)
    return messages
