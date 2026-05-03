import base64
import logging

from django.conf import settings

from workspace.ai.services.video import extract_video_frames

logger = logging.getLogger(__name__)

_TRUNCATE_BODY_LIMIT = 500  # max chars for old messages outside the recent window


def build_conversation_history(conversation_id, bot_profile, human_user):
    """Build the LLM message history for a conversation.

    Loads recent messages, reconstructs tool-call rounds, includes vision
    attachments when the bot supports it, and truncates old message bodies
    that fall outside the recent context window.

    Returns (history, summary_text).
    """
    from workspace.ai.models import ConversationSummary
    from workspace.chat.models import Message
    from workspace.users.services.settings import get_user_timezone

    recent_window = settings.AI_CHAT_CONTEXT_SIZE
    conv_summary = ConversationSummary.objects.filter(conversation_id=conversation_id).first()
    summary_text = conv_summary.content if conv_summary else ''

    all_msgs = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )
        .select_related('author', 'author__bot_profile')
        .prefetch_related('attachments')
        .order_by('-created_at')[:recent_window]
    )

    if summary_text and conv_summary.up_to:
        msgs_to_use = [m for m in all_msgs if m.created_at > conv_summary.up_to]
    else:
        msgs_to_use = all_msgs

    # Find the most recent user message that has visual attachments (image/video)
    _att_cache = {}
    last_visual_msg_uuid = None
    if bot_profile.supports_vision:
        for msg in msgs_to_use:  # newest first
            is_bot = hasattr(msg.author, 'bot_profile')
            if not is_bot:
                atts = list(msg.attachments.all())
                _att_cache[msg.uuid] = atts
                if any(att.is_image or att.is_video for att in atts):
                    last_visual_msg_uuid = str(msg.uuid)
                    break

    # Number of old messages (outside recent window) not covered by a summary
    truncate_count = max(0, len(msgs_to_use) - recent_window) if not summary_text else 0

    _user_tz = get_user_timezone(human_user) if human_user else None

    history = []
    for idx, msg in enumerate(reversed(msgs_to_use)):
        is_bot = hasattr(msg.author, 'bot_profile')
        role = 'assistant' if is_bot else 'user'
        body = msg.body

        if idx < truncate_count and len(body) > _TRUNCATE_BODY_LIMIT:
            body = body[:_TRUNCATE_BODY_LIMIT] + '…'

        # Inject a system message with the timestamp before each message
        # so the LLM has temporal context without polluting message content.
        local_dt = msg.created_at.astimezone(_user_tz) if _user_tz else msg.created_at
        history.append({'role': 'system', 'content': f'[{local_dt.strftime("%Y-%m-%d %H:%M")}]'})

        # Reconstruct tool call history for bot messages
        if is_bot and msg.tool_data:
            for td_round in msg.tool_data:
                assistant_msg = {
                    'role': 'assistant',
                    'content': td_round.get('assistant_content', ''),
                    'tool_calls': td_round['tool_calls'],
                }
                history.append(assistant_msg)
                for tr in td_round.get('results', []):
                    history.append({
                        'role': 'tool',
                        'tool_call_id': tr['tool_call_id'],
                        'content': tr['content'],
                    })
            history.append({'role': 'assistant', 'content': body})
            continue

        # Include visual media only from the most recent message that has them
        media_parts = []
        video_descriptions = []
        if not is_bot and str(msg.uuid) == last_visual_msg_uuid:
            for att in _att_cache.get(msg.uuid, msg.attachments.all()):
                if att.is_image:
                    try:
                        data = att.file.read()
                        b64 = base64.b64encode(data).decode()
                        media_parts.append({
                            'type': 'image_url',
                            'image_url': {
                                'url': f'data:{att.mime_type};base64,{b64}',
                            },
                        })
                    except Exception:
                        logger.warning('Could not read attachment %s', att.uuid)
                elif att.is_video:
                    frames, desc = extract_video_frames(att)
                    if desc:
                        video_descriptions.append(desc)
                    media_parts.extend(frames)

        if video_descriptions:
            # Attach the video metadata as a `user` message (not `system`):
            # `desc` includes attachment-derived text like att.original_name,
            # so giving it system-level priority is a prompt-injection vector.
            history.append({'role': 'user', 'content': '\n'.join(video_descriptions)})

        if media_parts:
            content = []
            if body:
                content.append({'type': 'text', 'text': body})
            content.extend(media_parts)
            history.append({'role': role, 'content': content})
        else:
            history.append({'role': role, 'content': body})

    return history, summary_text
