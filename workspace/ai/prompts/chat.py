from .base import build_context_block

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant integrated into a workspace application. "
    "You respond concisely and helpfully. You can answer questions, help with writing, "
    "and assist with various tasks. Respond in the same language as the user's message."
)


def build_chat_messages(system_prompt: str, history: list[dict], bot_name: str = '') -> list[dict]:
    """Build the messages list for the OpenAI API from conversation history.

    Args:
        system_prompt: The bot's system prompt.
        history: List of dicts with 'role' and 'content' keys.
        bot_name: The display name of the bot.
    """
    base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    context = build_context_block()
    if bot_name:
        context = f"Your name is {bot_name}.\n{context}"
    system_content = f"{base_prompt}\n\n{context}"
    messages = [{'role': 'system', 'content': system_content}]
    messages.extend(history)
    return messages
