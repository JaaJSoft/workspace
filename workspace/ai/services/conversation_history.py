import base64
import logging

from django.conf import settings

from workspace.ai.services.video import extract_video_frames
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


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
        from workspace.ai.tasks.captions import generate_attachment_caption

        generate_attachment_caption.delay(str(att.uuid))
        return f"[image: {att.original_name}]"
    return f"[image: {att.original_name} - {att.ai_description}]"


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


def _assistant_images_message(media_parts):
    # image_url parts are not universally accepted in assistant-role messages,
    # so the assistant's own images ride in a follow-up user message (same
    # role rationale as the video descriptions below).
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "[Images sent by the assistant in the message above]",
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

    Returns ``(history, summary_text)``.
    """
    from workspace.ai.models import ConversationSummary
    from workspace.chat.models import Message
    from workspace.users.services.settings import get_user_timezone

    recent_window = settings.AI_CHAT_CONTEXT_SIZE
    conv_summary = ConversationSummary.objects.filter(
        conversation_id=conversation_id
    ).first()
    summary_text = conv_summary.content if conv_summary else ""

    all_msgs = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )
        .select_related("author", "author__bot_profile")
        .prefetch_related("attachments")
        .order_by("-created_at")[:recent_window]
    )

    if summary_text and conv_summary.up_to:
        msgs_to_use = [m for m in all_msgs if m.created_at > conv_summary.up_to]
    else:
        msgs_to_use = all_msgs

    vision = bot_profile.supports_vision
    _att_cache = {}
    pixel_msg_uuids = []
    allowed_att_uuids = set()
    if vision:
        for msg in msgs_to_use:
            _att_cache[msg.uuid] = list(msg.attachments.all())
        pixel_msg_uuids, allowed_att_uuids = _visual_window(msgs_to_use, _att_cache)

    _user_tz = get_user_timezone(human_user) if human_user else None

    history = []
    for msg in reversed(msgs_to_use):
        is_bot = hasattr(msg.author, "bot_profile")
        role = "assistant" if is_bot else "user"
        body = msg.body

        # Inject a system message with the timestamp before each message
        # so the LLM has temporal context without polluting message content.
        local_dt = msg.created_at.astimezone(_user_tz) if _user_tz else msg.created_at
        history.append(
            {"role": "system", "content": f"[{local_dt.strftime('%Y-%m-%d %H:%M')}]"}
        )

        media_parts, video_descriptions, caption_notes = [], [], []
        if vision:
            in_window = str(msg.uuid) in pixel_msg_uuids
            media_parts, video_descriptions, caption_notes = _collect_media(
                msg, is_bot, in_window, allowed_att_uuids, _att_cache
            )
        body_text = body
        if caption_notes:
            body_text = (body + "\n" if body else "") + "\n".join(caption_notes)

        # Reconstruct tool call history for bot messages
        if is_bot and msg.tool_data:
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
                for tr in td_round.get("results", []):
                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr["tool_call_id"],
                            "content": tr["content"],
                        }
                    )
            history.append({"role": "assistant", "content": body_text})
            if media_parts:
                history.append(_assistant_images_message(media_parts))
            continue

        if video_descriptions:
            # Attach the video metadata as a `user` message (not `system`):
            # `desc` includes attachment-derived text like att.original_name,
            # so giving it system-level priority is a prompt-injection vector.
            history.append({"role": "user", "content": "\n".join(video_descriptions)})

        if is_bot:
            history.append({"role": "assistant", "content": body_text})
            if media_parts:
                history.append(_assistant_images_message(media_parts))
        elif media_parts:
            content = []
            if body_text:
                content.append({"type": "text", "text": body_text})
            content.extend(media_parts)
            history.append({"role": role, "content": content})
        else:
            history.append({"role": role, "content": body_text})

    return history, summary_text
