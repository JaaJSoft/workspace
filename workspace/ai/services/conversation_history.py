import base64
import logging
from datetime import timedelta
from itertools import pairwise
from typing import NamedTuple

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.text import Truncator

from workspace.ai.metrics import AI_HISTORY_TOOL_CHARS
from workspace.ai.prompts.base import sanitize_prompt_line
from workspace.ai.services.llm import truncate_tool_result
from workspace.ai.services.video import extract_video_frames
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# A gap shorter than this between two messages is the normal rhythm of a chat
# and goes unmentioned; past it the header spells the delay out.
GAP_NOTE_MIN = timedelta(hours=1)
# Characters of a bot message quoted back on the header of a reply to it.
QUOTE_LEN = 80


class ConversationHistory(NamedTuple):
    messages: list
    summary: str
    # The Message rows the history was built from, newest first: what a
    # caller describing "the state of the conversation" must read, so that
    # the description and the history never disagree.
    window: list


def _plural(count, unit):
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def _elapsed_phrase(delta):
    """Coarse wording of a delay: '2 days', '3 hours', '12 minutes'.

    Each unit rounds half up from the raw delay, never from the unit below,
    so a longer delay never reads shorter: 35 hours is "1 day", 36 hours
    "2 days", the way a person would say it.
    """
    seconds = delta.total_seconds()
    minutes = int(seconds / 60 + 0.5)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return _plural(minutes, "minute")
    hours = int(seconds / 3600 + 0.5)
    if hours < 24:
        return _plural(hours, "hour")
    return _plural(int(seconds / 86400 + 0.5), "day")


def _flat(value, max_len):
    """User-controlled text fit for the header: one line, and no ``]``
    that would close the bracketed header early."""
    text = " ".join(str(value or "").split())
    return sanitize_prompt_line(text, max_len).replace("]", "")


def _identity(user):
    """'Hana (@hana)': the same form the system prompt introduces the user with."""
    username = _flat(user.username, 100)
    display = _flat(user.get_full_name(), 100) or username
    return f"{display} (@{username})"


def _bot_message_sentence(msg, bot_user_id, prev_msg, window_uuids):
    """What a bot-authored message is, read off the AI task that posted it.

    Adjacency cannot tell: a reply follows another reply whenever the user
    deleted the message in between or sent two in quick succession, and a
    scheduled message can land right after the user wrote. The task record
    knows, and a message without one (purged, or an error notice) gets no
    claim at all rather than a guessed one.
    """
    if msg.author_id != bot_user_id:
        return f"Message from another bot, {_identity(msg.author)}."
    task = next(iter(msg.ai_tasks.all()), None)
    if task is None:
        return "Your message."
    if task.task_type == task.TaskType.AGENT:
        return "Message you sent on your own initiative, at a goal check-in."
    if "schedule_id" in task.input_data:
        return "Message you sent on your own initiative, as a scheduled message."
    trigger = task.input_data.get("message_id")
    if trigger and prev_msg is not None and trigger not in window_uuids:
        return "Your reply, to a message the user has since deleted."
    return "Your reply."


def _reply_sentence(msg, bot_user_id):
    replied = msg.reply_to
    if replied is None:
        return ""
    if replied.author_id == bot_user_id:
        # Only the bot's own words are quoted back: a quote of the user's
        # text would put user-controlled prose on a system line.
        quote = Truncator(_flat(replied.body, 400)).chars(QUOTE_LEN, truncate="...")
        if quote:
            return f'In reply to your message: "{quote}".'
        return "In reply to one of your messages."
    if replied.author_id == msg.author_id:
        return "In reply to one of their own earlier messages."
    who = _identity(replied.author)
    if hasattr(replied.author, "bot_profile"):
        return f"In reply to a message from another bot, {who}."
    return f"In reply to a message from {who}."


def _message_header(msg, bot_user_id, prev_msg, user_tz, att_cache, window_uuids):
    """The bracketed system line before a message: when it was sent, by whom,
    and how.

    Everything the model cannot read off the role alone is said here. Two
    assistant turns in a row are the case that matters: a scheduled message
    or a goal check-in lands right after the bot's previous reply, and a
    model trained on strict user/assistant alternation reads that second
    turn as the user's unless the line says whose it is.

    Names and file names are user-controlled and flattened to one line: a
    system line carries an authority a user turn does not, so a newline in
    a first name must not be able to forge an instruction on it. The whole
    line sits in one pair of brackets so that ``clean_llm_content`` drops an
    imitation of it whole, prose included.
    """
    local_dt = msg.created_at.astimezone(user_tz) if user_tz else msg.created_at
    parts = []

    if msg.kind == msg.Kind.SYSTEM:
        parts.append(
            "System notice about a call in the conversation, written by nobody."
        )
    elif hasattr(msg.author, "bot_profile"):
        parts.append(_bot_message_sentence(msg, bot_user_id, prev_msg, window_uuids))
    else:
        parts.append(f"Message from the user, {_identity(msg.author)}.")

    if prev_msg is not None:
        gap = msg.created_at - prev_msg.created_at
        if gap >= GAP_NOTE_MIN:
            parts.append(f"Sent {_elapsed_phrase(gap)} after the previous message.")

    if msg.kind != msg.Kind.SYSTEM and msg.author_id != bot_user_id:
        if reply := _reply_sentence(msg, bot_user_id):
            parts.append(reply)
        files = [
            a
            for a in att_cache.get(msg.uuid, [])
            if not (a.is_image or a.is_video or a.is_audio)
        ]
        if files:
            names = ", ".join(_flat(a.original_name, 60) for a in files)
            parts.append(f"Attached file(s): {names}.")
        if msg.edited_at:
            parts.append("Edited after sending.")

    return f"[{local_dt.strftime('%Y-%m-%d %H:%M')} | {' '.join(parts)}]"


def unprompted_run_note(window, bot_user_id):
    """Context sentence for a run nothing in the chat triggered.

    Scheduled messages and goal check-ins send the history exactly as a
    reply would, so the last turn the model reads is usually its own. Said
    outright, that stops it answering that turn as if the user had written.

    *window* is ``ConversationHistory.window``: the rows the history was
    built from, so the note describes what the model reads and nothing
    posted since.
    """
    now = timezone.now()
    lead = (
        "You are acting on your own initiative, not answering a new message: "
        "do not answer your own last message as if the user had sent it."
    )
    # A call notice is a row nobody wrote; it says nothing about who spoke last.
    spoken = [m for m in window if m.kind != m.Kind.SYSTEM]
    if not spoken:
        return (
            f"{lead} The conversation has no messages yet: you are writing "
            "first, and nobody is waiting for a reply."
        )
    last = spoken[0]
    ago = _elapsed_phrase(now - last.created_at)
    if last.author_id == bot_user_id:
        whose = "yours"
    elif hasattr(last.author, "bot_profile"):
        whose = "another bot's"
    else:
        return f"{lead} The last message in the conversation is the user's, sent {ago} ago."
    last_user = next((m for m in spoken if not hasattr(m.author, "bot_profile")), None)
    if last_user is None:
        since = "The user has not written in the recent history."
    else:
        since = f"The user's last message was {_elapsed_phrase(now - last_user.created_at)} ago."
    return f"{lead} The last message in the conversation is {whose}, sent {ago} ago. {since}"


def _replay_budget(turn_age):
    """Characters a tool result keeps, given how many bot turns ago it ran.

    Halving per turn: the turn the user is most likely following up on is
    replayed whole, while an old round decays towards a stub that still names
    the call behind it. The number of replayed turns is already bounded by
    AI_CHAT_CONTEXT_SIZE, so the total stays bounded too.
    """
    full = settings.AI_TOOL_RESULT_STORE_MAX_CHARS
    return max(full >> min(turn_age, 16), settings.AI_TOOL_RESULT_REPLAY_MIN_CHARS)


def _replay_results(td_round, budget):
    """Tool messages for one stored round, trimmed to *budget* characters."""
    from workspace.ai.tool_registry import tool_registry

    hints = {}
    messages = []
    for tr in td_round.get("results", []):
        content = tr["content"]
        if isinstance(content, str) and len(content) > budget:
            if not hints:
                hints = {
                    tc.get("id"): tool_registry.describe_call(
                        (tc.get("function") or {}).get("name") or "",
                        (tc.get("function") or {}).get("arguments") or "",
                    )
                    for tc in td_round.get("tool_calls") or []
                }
            content = truncate_tool_result(
                content, budget, hint=hints.get(tr["tool_call_id"], "")
            )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": content,
            }
        )
    return messages


def _image_pixel_part(att):
    """Return an image_url content part for an attachment, or None if unreadable."""
    try:
        data = att.file.read()
    except Exception:
        logger.warning("Could not read attachment %s", scrub(str(att.uuid)))
        return None
    b64 = base64.b64encode(data).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{att.mime_type};base64,{b64}"},
    }


def _image_note(att):
    """Textual stand-in for an image outside the pixel window.

    Self-healing: a missing caption re-enqueues the captioning task so the
    next turn has it (the task is idempotent and exits early once filled).
    """
    if not att.ai_description:
        from workspace.ai.tasks.captions import enqueue_caption_retry

        enqueue_caption_retry(att)
        return f"[image: {att.original_name}]"
    return f"[image: {att.original_name} - {att.ai_description}]"


def _heard_in(att):
    """What the user's recording says, transcribed once and stored on it.

    Empty when the deployment configures no recognition model, when the blob
    is gone, or when the backend failed. Nothing is stored in those cases, so
    the next turn of the conversation asks again.
    """
    if att.ai_description:
        return att.ai_description

    from workspace.ai.services.transcription import (
        ai_transcribe_audio,
        is_transcription_enabled,
    )

    if not is_transcription_enabled():
        return ""
    try:
        with att.file.open("rb") as recording:
            data = recording.read()
    except (FileNotFoundError, OSError) as exc:
        logger.warning(
            "Unreadable voice message %s: %s", scrub(att.file.name), scrub(str(exc))
        )
        return ""
    if not data:
        return ""

    text = ai_transcribe_audio(data)
    if text:
        from workspace.chat.models import MessageAttachment

        MessageAttachment.objects.filter(uuid=att.uuid).update(ai_description=text)
    return text


def _audio_notes(atts, is_bot):
    """Textual stand-ins for the voice messages of one chat message.

    Audio never reaches the model - the chat model has no ears. What the bot
    said out loud is replayed from the text the speech tool stored on the
    attachment, so it does not repeat itself; what the user said is replayed
    from the recognition model, and only announced when there is none, since
    silence would read as an empty message.

    The note says the words were transcribed rather than typed: a bot that
    knows it was spoken to can answer out loud, and can ask again about a
    word the recognition got wrong instead of answering the wrong question.
    """
    notes = []
    for att in atts:
        if not att.is_audio:
            continue
        if not is_bot:
            heard = _heard_in(att)
            notes.append(
                f"[Voice message from the user, transcribed automatically "
                f'(a word may be wrong): "{heard}"]'
                if heard
                else "[The user sent a voice message. You cannot listen to it — "
                "say so if it matters.]"
            )
        elif att.ai_description:
            notes.append(f'[Voice message you sent: "{att.ai_description}"]')
        else:
            notes.append("[You sent a voice message here.]")
    return notes


def _visual_window(msgs_newest_first, att_cache):
    """Pick the last 2 messages (any author) with visual attachments.

    Returns (pixel_msg_uuids, allowed_att_uuids): which messages may inject
    pixels, and which image attachments fit the AI_VISION_MAX_IMAGES budget
    (newest first, so the cap drops the oldest images).
    """
    pixel_msg_uuids = []
    allowed = set()
    budget = settings.AI_VISION_MAX_IMAGES
    for msg in msgs_newest_first:
        atts = att_cache.get(msg.uuid, [])
        if not any(a.is_image or a.is_video for a in atts):
            continue
        pixel_msg_uuids.append(str(msg.uuid))
        for att in atts:
            if att.is_image and budget > 0:
                allowed.add(att.uuid)
                budget -= 1
        if len(pixel_msg_uuids) == 2:
            break
    return pixel_msg_uuids, allowed


def _collect_media(msg, is_bot, in_window, allowed_att_uuids, att_cache):
    """Split a message's attachments into pixel parts, video parts and notes."""
    media_parts = []
    video_descriptions = []
    caption_notes = []
    for att in att_cache.get(msg.uuid, []):
        if att.is_image:
            part = None
            if in_window and att.uuid in allowed_att_uuids:
                part = _image_pixel_part(att)
            if part:
                media_parts.append(part)
            else:
                caption_notes.append(_image_note(att))
        elif att.is_video and in_window and not is_bot:
            frames, desc = extract_video_frames(att)
            if desc:
                video_descriptions.append(desc)
            media_parts.extend(frames)
    return media_parts, video_descriptions, caption_notes


def _assistant_images_message(media_parts, caption_notes=()):
    # image_url parts are not universally accepted in assistant-role messages,
    # so the assistant's own images ride in a follow-up user message (same
    # role rationale as the video descriptions below). Caption notes ride here
    # too: an "[image: ...]" marker in an assistant turn reads as the bot's own
    # writing style and gets imitated in later replies.
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "\n".join(
                    ["[Images sent by the assistant in the message above]"]
                    + list(caption_notes)
                ),
            },
            *media_parts,
        ],
    }


def build_conversation_history(conversation_id, bot_profile, human_user):
    """Build the LLM message history for a conversation.

    Loads up to ``AI_CHAT_CONTEXT_SIZE`` recent messages, reconstructs
    tool-call rounds, and includes vision attachments when the bot
    supports them. Older messages that fall outside the window are
    represented by ``ConversationSummary`` (refreshed by
    ``ai.update_conversation_summary``).
    """
    from workspace.ai.models import AITask, ConversationSummary
    from workspace.chat.models import Message
    from workspace.users.services.settings import get_user_timezone

    recent_window = settings.AI_CHAT_CONTEXT_SIZE
    conv_summary = ConversationSummary.objects.filter(
        conversation_id=conversation_id
    ).first()
    summary_text = conv_summary.content if conv_summary else ""

    # The task that posted a bot message says what the message was (a
    # reply, a scheduled message, a check-in); its raw_messages are large
    # and not wanted here.
    posting_tasks = Prefetch(
        "ai_tasks",
        queryset=AITask.objects.only("uuid", "task_type", "input_data", "chat_message"),
    )
    all_msgs = list(
        Message.objects.filter(conversation_id=conversation_id, deleted_at__isnull=True)
        .select_related(
            "author",
            "author__bot_profile",
            "reply_to__author",
            "reply_to__author__bot_profile",
        )
        .prefetch_related("attachments", posting_tasks)
        .order_by("-created_at")[:recent_window]
    )

    if summary_text and conv_summary.up_to:
        msgs_to_use = [m for m in all_msgs if m.created_at > conv_summary.up_to]
    else:
        msgs_to_use = all_msgs

    # Gaps are measured against the message before, even one the summary
    # swallowed: the first message after the cutoff still came after it.
    prev_of = {m.uuid: prev for prev, m in pairwise(reversed(all_msgs))}
    window_uuids = {str(m.uuid) for m in all_msgs}

    vision = bot_profile.supports_vision
    # Populated whatever the bot's vision support: voice messages are read
    # from it too, and hearing has nothing to do with seeing.
    _att_cache = {msg.uuid: list(msg.attachments.all()) for msg in msgs_to_use}
    if vision:
        # Only defined (and only read) when vision is on.
        pixel_msg_uuids, allowed_att_uuids = _visual_window(msgs_to_use, _att_cache)

    _user_tz = get_user_timezone(human_user) if human_user else None

    # msgs_to_use is newest first, so enumerating it counts bot turns backwards
    # from the one being answered - the age each replay budget is derived from.
    tool_turn_age = {
        m.uuid: age
        for age, m in enumerate(
            m
            for m in msgs_to_use
            if isinstance(m.tool_data, list) and hasattr(m.author, "bot_profile")
        )
    }

    history = []
    replayed_tool_chars = 0
    for msg in reversed(msgs_to_use):
        is_bot = hasattr(msg.author, "bot_profile")
        role = "assistant" if is_bot else "user"
        body = msg.body

        # Timestamp and sender ride on a system line before each message, so
        # the message content itself stays exactly what was written.
        history.append(
            {
                "role": "system",
                "content": _message_header(
                    msg,
                    bot_profile.user_id,
                    prev_of.get(msg.uuid),
                    _user_tz,
                    _att_cache,
                    window_uuids,
                ),
            }
        )

        media_parts, video_descriptions, caption_notes = [], [], []
        if vision:
            in_window = str(msg.uuid) in pixel_msg_uuids
            media_parts, video_descriptions, caption_notes = _collect_media(
                msg, is_bot, in_window, allowed_att_uuids, _att_cache
            )
        audio_notes = _audio_notes(_att_cache.get(msg.uuid, []), is_bot)
        body_text = body
        inline_notes = caption_notes + audio_notes if not is_bot else []
        if inline_notes:
            body_text = (body + "\n" if body else "") + "\n".join(inline_notes)

        # Reconstruct tool call history for bot messages
        if is_bot and msg.tool_data:
            budget = _replay_budget(tool_turn_age.get(msg.uuid, 0))
            for td_round in msg.tool_data:
                tool_calls = td_round.get("tool_calls")
                if not tool_calls:
                    # Tool-less rounds only carry thinking for the UI
                    # timeline; reasoning is never replayed to the LLM.
                    continue
                assistant_msg = {
                    "role": "assistant",
                    "content": td_round.get("assistant_content", ""),
                    "tool_calls": tool_calls,
                }
                history.append(assistant_msg)
                for tool_msg in _replay_results(td_round, budget):
                    replayed_tool_chars += len(tool_msg["content"])
                    history.append(tool_msg)
            history.append({"role": "assistant", "content": body_text})
            if media_parts or caption_notes:
                history.append(_assistant_images_message(media_parts, caption_notes))
            if audio_notes:
                history.append({"role": "user", "content": "\n".join(audio_notes)})
            continue

        if video_descriptions:
            # Attach the video metadata as a `user` message (not `system`):
            # `desc` includes attachment-derived text like att.original_name,
            # so giving it system-level priority is a prompt-injection vector.
            history.append({"role": "user", "content": "\n".join(video_descriptions)})

        if is_bot:
            history.append({"role": "assistant", "content": body_text})
            if media_parts or caption_notes:
                history.append(_assistant_images_message(media_parts, caption_notes))
            if audio_notes:
                history.append({"role": "user", "content": "\n".join(audio_notes)})
        elif media_parts:
            content = []
            if body_text:
                content.append({"type": "text", "text": body_text})
            content.extend(media_parts)
            history.append({"role": role, "content": content})
        else:
            history.append({"role": role, "content": body_text})

    AI_HISTORY_TOOL_CHARS.observe(replayed_tool_chars)
    return ConversationHistory(history, summary_text, all_msgs)
