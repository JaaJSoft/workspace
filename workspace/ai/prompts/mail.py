from .base import truncate_text

SUMMARIZE_SYSTEM = (
    "You are an email summarization assistant. Provide a concise summary "
    "of the email in 2-5 bullet points. Focus on key information, action items, "
    "and decisions. Respond in the same language as the email."
)

COMPOSE_SYSTEM = (
    "You are an email composition assistant. Write professional, clear emails "
    "based on the user's instructions. Match the tone and formality level "
    "indicated by the user. Respond in the same language as the instructions."
)

REPLY_SYSTEM = (
    "You are an email reply assistant. Write a reply to the email below based on "
    "the user's instructions. Keep the tone appropriate for the original email's "
    "formality level. Respond in the same language as the original email."
)


def build_summarize_messages(subject: str, body: str) -> list[dict]:
    """Build messages for email summarization."""
    content = f"Subject: {subject}\n\n{truncate_text(body)}"
    return [
        {'role': 'system', 'content': SUMMARIZE_SYSTEM},
        {'role': 'user', 'content': f"Summarize this email:\n\n{content}"},
    ]


def build_compose_messages(instructions: str, context: str = '') -> list[dict]:
    """Build messages for email composition."""
    user_msg = f"Instructions: {instructions}"
    if context:
        user_msg += f"\n\nContext:\n{truncate_text(context)}"
    return [
        {'role': 'system', 'content': COMPOSE_SYSTEM},
        {'role': 'user', 'content': user_msg},
    ]


def build_reply_messages(instructions: str, original_subject: str, original_body: str) -> list[dict]:
    """Build messages for email reply generation."""
    original = f"Subject: {original_subject}\n\n{truncate_text(original_body)}"
    return [
        {'role': 'system', 'content': REPLY_SYSTEM},
        {'role': 'user', 'content': f"Original email:\n{original}\n\nReply instructions: {instructions}"},
    ]
