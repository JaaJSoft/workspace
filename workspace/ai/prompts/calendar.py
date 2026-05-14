"""Prompt construction for LLM event extraction from mail."""

from workspace.ai.prompts.mail import INJECTION_GUARD

MAX_BODY_CHARS = 4000  # per message; threads can have many messages.

SYSTEM_PROMPT = (
    "You extract calendar events from email threads. Output rules:\n"
    "1. Return a JSON array of events. Empty array means no event.\n"
    "2. ONLY extract events that are CONFIRMED and SCHEDULED: a meeting "
    "with a date and time, a flight, a train ticket, a concert booking, "
    "a medical appointment, a restaurant reservation.\n"
    "3. REJECT vague proposals (\"we should meet sometime\"), questions "
    "(\"can we catch up next week?\"), brainstorms, marketing or "
    "promotional fluff, and anything where the user has not committed.\n"
    "4. Each event MUST have: title (string), start (ISO 8601 with "
    "timezone), end (ISO 8601 with timezone OR null), all_day (bool), "
    "location (string, may be empty), description (string, may be "
    "empty), confidence (one of 'high', 'medium', 'low'), reasoning "
    "(short free text explaining WHY you decided this is a real event).\n"
    "5. If a date is mentioned without time, set all_day=true.\n"
    "6. If the email doesn't specify a timezone, assume the recipient's "
    "local timezone (use UTC if unknown).\n"
    "7. Do NOT extract recurring events as a series; if a mail describes "
    "a recurrence, extract only the next occurrence.\n"
    "Output format: a JSON array, no other text, no markdown fences.\n"
    "Example: [{\"title\":\"Train Paris-Lyon\",\"start\":"
    "\"2026-06-01T08:00:00+02:00\",\"end\":\"2026-06-01T10:00:00+02:00\","
    "\"all_day\":false,\"location\":\"Gare de Lyon\",\"description\":\"\","
    "\"confidence\":\"high\",\"reasoning\":\"Booking confirmation with "
    "departure time and station.\"}]"
)


def build_event_extraction_messages(thread_messages: list) -> list[dict]:
    """Render a chronological thread into the chat-format messages
    expected by call_llm().

    thread_messages is a list of MailMessage instances (or compatible
    objects with subject, body_text, body_html, from_address, date),
    ordered oldest-first. Each body is truncated to MAX_BODY_CHARS.
    """
    rendered = []
    for i, m in enumerate(thread_messages, 1):
        body = (m.body_text or m.body_html or '').strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + '... [truncated]'
        from_addr = m.from_address if isinstance(m.from_address, dict) else {}
        sender = from_addr.get('email', '') or '(unknown)'
        date_str = m.date.isoformat() if m.date else ''
        rendered.append(
            f"[Message {i} | {date_str} | From: {sender} | Subject: {m.subject}]\n{body}"
        )
    thread_block = '\n\n---\n\n'.join(rendered)

    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': (
            f"Extract events from this email thread:\n\n"
            f"<untrusted-content>\n{thread_block}\n</untrusted-content>\n\n"
            f"{INJECTION_GUARD}"
        )},
    ]
