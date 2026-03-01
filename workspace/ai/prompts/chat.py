DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant integrated into a workspace application. "
    "You respond concisely and helpfully. You can answer questions, help with writing, "
    "and assist with various tasks. Respond in the same language as the user's message."
)


def build_chat_messages(
    system_prompt: str,
    history: list[dict],
    new_messages: list[dict] | None = None,
) -> list[dict]:
    """Build the messages list for the OpenAI API.

    Args:
        system_prompt: The bot's system prompt.
        history: Past conversation exchanges (already answered by the bot).
        new_messages: New user messages the bot hasn't responded to yet.
            When multiple messages are pending, they are consolidated into
            a single user message so the bot can address them all at once.
    """
    messages = [{'role': 'system', 'content': system_prompt or DEFAULT_SYSTEM_PROMPT}]
    messages.extend(history)

    if new_messages:
        if len(new_messages) == 1:
            messages.append(new_messages[0])
        else:
            # Consolidate multiple unanswered messages into one block
            # so the model sees them as a coherent batch to address.
            parts = []
            for i, msg in enumerate(new_messages, 1):
                parts.append(f"[Message {i}]\n{msg['content']}")
            combined = "\n\n".join(parts)
            messages.append({
                'role': 'user',
                'content': (
                    f"The user sent {len(new_messages)} messages while you were "
                    f"processing. Address all of them in a single response:\n\n"
                    f"{combined}"
                ),
            })

    return messages
