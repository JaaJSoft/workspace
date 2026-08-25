"""AI tools for the Chat module."""

import json
import uuid as uuid_lib

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool

# Hard caps for the transcript tools. A conversation grows without bound; a
# tool result cannot. The character budget is the one that matters — a cap on
# the message count says nothing about how long each message is.
READ_MAX_MESSAGES = 50
READ_DEFAULT_MESSAGES = 30
READ_MAX_CHARS = 12000
READ_MAX_BODY_CHARS = 1000


class SearchMessagesParams(BaseModel):
    query: str = Field(description="The search term to look for in message content.")
    conversation_only: bool = Field(
        default=False, description="If true, search only the current conversation."
    )
    author: str = Field(default="", description="Filter by author username.")
    date_range: str = Field(
        default="", description="Filter by date range: today, 7d, 30d."
    )
    has_files: bool = Field(
        default=False,
        description="If true, only return messages with file attachments.",
    )
    has_images: bool = Field(
        default=False,
        description="If true, only return messages with image attachments.",
    )


class AskUserQuestionParams(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=500,
        description="The question to ask the user, written in their language.",
    )
    options: list[str] = Field(
        min_length=2,
        max_length=6,
        description=(
            "2-6 short, mutually exclusive answer suggestions. The user can "
            "also type a free-form reply."
        ),
    )


class ReadConversationParams(BaseModel):
    conversation_id: uuid_lib.UUID = Field(
        description="UUID of the conversation to read, as returned by search_messages."
    )
    limit: int = Field(
        default=READ_DEFAULT_MESSAGES,
        description=(
            f"How many of the most recent messages to return "
            f"(1-{READ_MAX_MESSAGES}, default {READ_DEFAULT_MESSAGES})."
        ),
    )


class SummarizeConversationParams(BaseModel):
    conversation_id: uuid_lib.UUID = Field(
        description="UUID of the conversation to summarize, as returned by search_messages."
    )


def _format_message(msg, user_tz):
    """Render one message as a transcript entry."""
    name = msg.author.get_full_name() or msg.author.username
    author = f"[Bot] {name}" if hasattr(msg.author, "bot_profile") else name

    body = msg.body
    if len(body) > READ_MAX_BODY_CHARS:
        body = body[:READ_MAX_BODY_CHARS] + "…"
    markers = [f"[attachment: {a.original_name}]" for a in msg.attachments.all()]
    if markers:
        body = "\n".join([body, *markers]) if body else "\n".join(markers)

    return {
        "timestamp": msg.created_at.astimezone(user_tz).strftime("%Y-%m-%d %H:%M"),
        "author": author,
        "body": body,
    }


def _read_transcript(conversation_id, user_tz, limit):
    """Return ``(entries, older_omitted)`` for the tail of a conversation.

    Entries come back oldest-first. The character budget is spent from the
    newest message backwards, so what falls off the end is the oldest
    context — the same trade-off the LLM history window makes.
    """
    from workspace.chat.models import Message

    rows = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )
        .select_related("author", "author__bot_profile")
        .prefetch_related("attachments")
        .order_by("-created_at")[: limit + 1]
    )
    # One row over the limit only tells us older messages exist; it is never
    # rendered.
    older_omitted = len(rows) > limit
    rows = rows[:limit]

    budget = READ_MAX_CHARS
    entries = []
    for msg in rows:
        entry = _format_message(msg, user_tz)
        cost = len(entry["body"]) + len(entry["author"])
        if entries and cost > budget:
            older_omitted = True
            break
        budget -= cost
        entries.append(entry)

    entries.reverse()
    return entries, older_omitted


class ChatToolProvider(ToolProvider):
    @tool(
        badge_icon="🔍",
        badge_label="Searched messages",
        badge_running_label="Searching messages",
        detail_key="query",
        params=SearchMessagesParams,
        concurrent=True,
    )
    def search_messages(self, args, user, bot, conversation_id, context):
        """Search chat messages across all your conversations, or within the current one. \
Returns up to 20 matches with author, timestamp, conversation, and content. \
Call this when the user asks about something said in chat, wants to find a message, \
or references a past discussion."""
        query = args.query.strip()
        if not query:
            return "Error: query is required"

        from datetime import timedelta

        from django.utils import timezone

        from workspace.chat.services.message_search import search_messages_qs
        from workspace.users.services.settings import get_user_timezone

        # Tools run in Celery with no request middleware, so the active
        # timezone is UTC: resolve the user's stored zone explicitly.
        user_tz = get_user_timezone(user)

        conv_only = args.conversation_only
        if conv_only and conversation_id:
            qs = search_messages_qs(user, query, conversation_id=conversation_id)
        else:
            qs = search_messages_qs(user, query)
        qs = qs.select_related("author", "conversation")

        author = args.author.strip()
        if author:
            qs = qs.filter(author__username__iexact=author)

        date_range = args.date_range.strip()
        if date_range:
            now = timezone.now()
            if date_range == "today":
                day_start = now.astimezone(user_tz).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                qs = qs.filter(
                    created_at__gte=day_start,
                    created_at__lt=day_start + timedelta(days=1),
                )
            elif date_range == "7d":
                qs = qs.filter(created_at__gte=now - timedelta(days=7))
            elif date_range == "30d":
                qs = qs.filter(created_at__gte=now - timedelta(days=30))

        if args.has_files:
            qs = qs.filter(attachments__isnull=False).distinct()
        if args.has_images:
            qs = qs.filter(attachments__mime_type__startswith="image/").distinct()

        matches = qs[:20]
        if not matches:
            return f'No messages found matching "{query}".'

        results = []
        for msg in matches:
            author_name = msg.author.get_full_name() or msg.author.username
            conv_name = msg.conversation.title or "DM"
            snippet = msg.body[:200]
            ts = msg.created_at.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
            results.append(
                {
                    "timestamp": ts,
                    "author": author_name,
                    "conversation": conv_name,
                    "conversation_id": str(msg.conversation_id),
                    "body": snippet,
                }
            )
        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="📖",
        badge_label="Read a conversation",
        badge_running_label="Reading a conversation",
        params=ReadConversationParams,
        concurrent=True,
    )
    def read_conversation(self, args, user, bot, conversation_id, context):
        """Read the most recent messages of one of the user's conversations, in order. \
Call this after search_messages when a single matching message is not enough to know what \
was decided, or when the user refers to a discussion held elsewhere. Returns at most \
50 messages, oldest first, truncated to fit a fixed size budget."""
        from workspace.chat.services.conversations import get_active_membership
        from workspace.users.services.settings import get_user_timezone

        # The *user's* membership is what grants access here, not the bot's:
        # the user belongs to conversations this bot was never added to, and
        # reading one on their behalf is legitimate.
        membership = get_active_membership(user, args.conversation_id)
        if not membership:
            return "Error: no such conversation, or you are not a member of it."

        entries, older_omitted = _read_transcript(
            args.conversation_id,
            get_user_timezone(user),
            max(1, min(args.limit, READ_MAX_MESSAGES)),
        )
        if not entries:
            return "This conversation has no messages yet."

        return json.dumps(
            {
                "conversation": membership.conversation.title or "DM",
                "messages": entries,
                "older_messages_omitted": older_omitted,
            },
            ensure_ascii=False,
        )

    @tool(
        badge_icon="📝",
        badge_label="Summarized a conversation",
        badge_running_label="Summarizing a conversation",
        params=SummarizeConversationParams,
    )
    def summarize_conversation(self, args, user, bot, conversation_id, context):
        """Summarize one of the user's conversations. Call this instead of read_conversation \
when the discussion is long and you need what it was about rather than what was literally said. \
The summary covers the older messages only — read_conversation gives you the recent ones."""
        from workspace.ai.models import ConversationSummary
        from workspace.ai.services.chat_summary import update_summary
        from workspace.chat.services.conversations import get_active_membership
        from workspace.users.services.settings import get_user_timezone

        membership = get_active_membership(user, args.conversation_id)
        if not membership:
            return "Error: no such conversation, or you are not a member of it."

        conv_id = str(args.conversation_id)
        title = membership.conversation.title or "DM"

        # Returns without calling the model when the stored summary already
        # covers everything outside the recent window; otherwise it only
        # summarises what arrived since ``up_to``.
        result = update_summary(conv_id)
        stored = ConversationSummary.objects.filter(conversation_id=conv_id).first()
        content = stored.content if stored else ""

        if content:
            return json.dumps(
                {
                    "conversation": title,
                    "summary": content,
                    "covers": (
                        "older messages only — call read_conversation for the "
                        "most recent ones"
                    ),
                },
                ensure_ascii=False,
            )

        if result.get("status") == "error":
            return f"Error: could not summarize this conversation — {result['error']}"

        # Nothing was ever summarised because the conversation is short enough
        # to be read in full: hand back the transcript rather than billing a
        # model call for a handful of messages.
        entries, older_omitted = _read_transcript(
            args.conversation_id, get_user_timezone(user), READ_MAX_MESSAGES
        )
        if not entries:
            return "This conversation has no messages yet."
        return json.dumps(
            {
                "conversation": title,
                "note": "Short conversation — here it is in full instead of a summary.",
                "messages": entries,
                "older_messages_omitted": older_omitted,
            },
            ensure_ascii=False,
        )

    @tool(
        badge_icon="💬",
        badge_label="Asked the user",
        badge_running_label="Asking the user",
        detail_key="question",
        params=AskUserQuestionParams,
    )
    def ask_user_question(self, args, user, bot, conversation_id, context):
        """Ask the user a clarifying question with 2-6 suggested answers. \
Use when you need a piece of information from the user and there's a small, \
discrete set of likely answers. Do NOT use for open-ended questions or when \
free-form text is clearly better. The user can click an option OR type their \
own answer."""
        seen = []
        for opt in args.options:
            o = opt.strip()
            if o and o not in seen:
                seen.append(o)
        if len(seen) < 2:
            return "Error: at least 2 distinct, non-empty options are required."

        question_text = args.question.strip()
        if not question_text:
            return "Error: question cannot be empty or whitespace-only."

        context.setdefault(
            "question",
            {
                "question": question_text,
                "options": seen[:6],
            },
        )
        context["stop_after_round"] = True
        return "Question presented to the user. Awaiting reply."
