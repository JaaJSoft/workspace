"""Prompt construction for LLM event extraction from mail."""

from zoneinfo import ZoneInfo

from django.utils import timezone

from workspace.ai.prompts.mail import INJECTION_GUARD

_UTC = ZoneInfo("UTC")

MAX_BODY_CHARS = 4000  # per message; threads can have many messages.

SYSTEM_PROMPT = (
    "You extract calendar events from email threads. Output rules:\n"
    "1. Return a JSON array of events. Empty array means no event.\n"
    "2. ONLY extract events that are CONFIRMED and SCHEDULED: a meeting "
    "with a date and time, a flight, a train ticket, a concert booking, "
    "a medical appointment, a restaurant reservation.\n"
    '3. REJECT vague proposals ("we should meet sometime"), questions '
    '("can we catch up next week?"), brainstorms, marketing or '
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
    'Example: [{"title":"Train Paris-Lyon","start":'
    '"2026-06-01T08:00:00+02:00","end":"2026-06-01T10:00:00+02:00",'
    '"all_day":false,"location":"Gare de Lyon","description":"",'
    '"confidence":"high","reasoning":"Booking confirmation with '
    'departure time and station."}]'
)


def build_event_extraction_messages(
    thread_messages: list,
    user_tz: ZoneInfo | None = None,
) -> list[dict]:
    """Render a chronological thread into the chat-format messages
    expected by call_llm().

    thread_messages is a list of MailMessage instances (or compatible
    objects with subject, body_text, body_html, from_address, date),
    ordered oldest-first. Each body is truncated to MAX_BODY_CHARS.

    user_tz is the recipient's local timezone (from get_user_timezone).
    Injected into the prompt so the LLM resolves "8h" as 08:00 in that
    zone instead of UTC.

    Relative references ("demain", "tomorrow", "next Tuesday") are
    anchored on the date of the MOST RECENT message in the thread - not
    on the server's "now". Otherwise an email sent in March saying
    "rdv demain a 8h" but processed in May would be interpreted as
    May+1 instead of March+1.
    """
    rendered = []
    for i, m in enumerate(thread_messages, 1):
        body = (m.body_text or m.body_html or "").strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "... [truncated]"
        from_addr = m.from_address if isinstance(m.from_address, dict) else {}
        sender = from_addr.get("email", "") or "(unknown)"
        date_str = m.date.isoformat() if m.date else ""
        rendered.append(
            f"[Message {i} | {date_str} | From: {sender} | Subject: {m.subject}]\n{body}"
        )
    thread_block = "\n\n---\n\n".join(rendered)
    tz = user_tz or _UTC
    anchor_dt = (
        next((m.date for m in reversed(thread_messages) if m.date), None)
        or timezone.now()
    )
    anchor = anchor_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    tz_name = str(tz)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Resolve relative references ('tomorrow', 'demain', 'next "
                f"Tuesday', 'in three weeks') in each message against the "
                f"date that message was sent (the ISO timestamp shown after "
                f"'[Message N |'), NOT against today's date. The most "
                f"recent message in this thread was sent on {anchor} "
                f"({tz_name}); use that as the default anchor. When an "
                f"email mentions a time WITHOUT an explicit timezone (e.g., "
                f"'8h', '3pm'), interpret it in {tz_name}, NOT in UTC.\n\n"
                f"Extract events from this email thread:\n\n"
                f"<untrusted-content>\n{thread_block}\n</untrusted-content>\n\n"
                f"{INJECTION_GUARD}"
            ),
        },
    ]
