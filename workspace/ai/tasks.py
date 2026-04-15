import base64
import json
import logging
import os
import re
import subprocess
import tempfile

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>[\s\S]*?</think>\s*', re.IGNORECASE)
_RAW_TOOL_CALL_RE = re.compile(r'</?tool_call>', re.IGNORECASE)
# Matches timestamp prefixes leaked by the LLM, with or without brackets:
# "[2026-04-10 20:07] ..." or "2026-04-10 20:07 ..."
_TIMESTAMP_PREFIX_RE = re.compile(r'^\[?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]?\s*')
_TRUNCATE_BODY_LIMIT = 500  # max chars for old messages outside the recent window
_SUMMARY_BUFFER = 10  # re-summarise when unsummarised old messages exceed window by this many
_VIDEO_MAX_FRAMES = 30  # cap frames sent to the model to limit context size


def _clean_llm_content(content: str) -> str:
    """Strip artifacts that LLMs sometimes leak into their replies."""
    content = _RAW_TOOL_CALL_RE.sub('', content)
    content = _TIMESTAMP_PREFIX_RE.sub('', content)
    return content.strip()


def _get_video_duration(video_path):
    """Return video duration in seconds using ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _extract_video_frames(att):
    """Extract evenly-spaced frames from a video attachment (max _VIDEO_MAX_FRAMES).

    Returns (frame_parts, description) where frame_parts is a list of image_url
    content parts and description is a string summarising the video for the model.
    """
    parts = []
    description = None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, 'input.vid')
            with open(video_path, 'wb') as f:
                for chunk in att.file.chunks():
                    f.write(chunk)

            duration = _get_video_duration(video_path)
            if duration and duration > _VIDEO_MAX_FRAMES:
                fps = _VIDEO_MAX_FRAMES / duration
            else:
                fps = 1

            out_pattern = os.path.join(tmpdir, 'frame_%04d.jpg')
            subprocess.run(
                [
                    'ffmpeg', '-i', video_path,
                    '-vf', f'fps={fps}',
                    '-q:v', '8',
                    '-frames:v', str(_VIDEO_MAX_FRAMES),
                    out_pattern,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=120,
            )
            frame_files = sorted(
                f for f in os.listdir(tmpdir) if f.startswith('frame_') and f.endswith('.jpg')
            )
            if frame_files:
                dur_str = f'{duration:.0f}s' if duration else 'unknown duration'
                interval = duration / len(frame_files) if duration else 1
                description = (
                    f'The user attached a video: "{att.original_name}" '
                    f'(duration: {dur_str}). Since you cannot watch videos directly, '
                    f'it has been converted into {len(frame_files)} frames '
                    f'(1 frame every {interval:.1f}s) shown in chronological order '
                    f'in the next message. Analyze these frames to understand '
                    f'what happens in the video.'
                )
            for fname in frame_files:
                fpath = os.path.join(tmpdir, fname)
                with open(fpath, 'rb') as fh:
                    b64 = base64.b64encode(fh.read()).decode()
                parts.append({
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
                })
    except Exception:
        logger.warning('Could not extract frames from video %s', att.uuid)
    return parts, description


def _serialize_response(result):
    """Serialize an _call_llm result dict for storage."""
    tc = result.get('tool_calls')
    return {
        'content': result.get('content', ''),
        'tool_calls': [
            {'id': c.id, 'name': c.function.name, 'arguments': c.function.arguments}
            for c in tc
        ] if tc else None,
        'model': result.get('model', ''),
        'prompt_tokens': result.get('prompt_tokens'),
        'completion_tokens': result.get('completion_tokens'),
    }


def _sanitize_messages_for_storage(messages):
    """Strip large base64 image data and truncate huge text from messages."""
    sanitized = []
    for msg in messages:
        content = msg.get('content')
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and (
                    part.get('type') == 'image_url' or 'image_url' in part
                ):
                    parts.append({'type': 'image_url', 'image_url': '[stripped]'})
                else:
                    parts.append(part)
            sanitized.append({**msg, 'content': parts})
        elif isinstance(content, str) and len(content) > 50_000:
            sanitized.append({**msg, 'content': content[:50_000] + '… [truncated]'})
        else:
            sanitized.append(msg)
    return sanitized


def _truncate_tool_result(text, max_len=2000):
    """Truncate tool result strings for storage, stripping image data."""
    if not text:
        return text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get('type') == 'image':
            return json.dumps({'type': 'image', 'data': '[stripped]'})
    except (json.JSONDecodeError, TypeError):
        pass
    if len(text) > max_len:
        return text[:max_len] + '… [truncated]'
    return text


def _build_tool_content(tool_result: str):
    """Convert a tool result string into API content, handling image payloads."""
    try:
        parsed = json.loads(tool_result)
        if isinstance(parsed, dict) and parsed.get('type') == 'image':
            mime = parsed.get('mime_type', 'image/webp')
            data = parsed['data']
            return [
                {'type': 'text', 'text': 'Here is the image:'},
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}},
            ]
    except (json.JSONDecodeError, KeyError):
        pass
    return tool_result


def _strip_thinking(content: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_RE.sub('', content).strip()


def _extract_text_tool_calls(content: str):
    """Parse tool calls that a model emitted as plain text instead of structured output.

    Handles two formats:
    - ``{"tool": "name", "prompt": "...", ...}`` (shorthand some models use)
    - ``{"name": "name", "arguments": {...}}`` (OpenAI-like)

    Returns a list of (name, arguments_json) tuples and the remaining text,
    or (None, content) if nothing was found.
    """
    cleaned = _RAW_TOOL_CALL_RE.sub('', content).strip()

    calls = []
    remaining = cleaned

    for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', cleaned):
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue

        if 'tool' in parsed:
            name = parsed.pop('tool')
            calls.append((name, json.dumps(parsed)))
            remaining = remaining.replace(match.group(), '').strip()
        elif 'name' in parsed and 'arguments' in parsed:
            args = parsed['arguments']
            args_json = args if isinstance(args, str) else json.dumps(args)
            calls.append((parsed['name'], args_json))
            remaining = remaining.replace(match.group(), '').strip()

    if calls:
        logger.info('Parsed %d tool call(s) from text content', len(calls))
        return calls, remaining
    return None, content


def _call_llm(messages: list[dict], model: str | None = None, max_tokens: int | None = None, tools: list | None = None) -> dict:
    """Call an LLM via OpenAI SDK and return a dict with content and usage info."""
    from workspace.ai.client import get_ai_client

    client = get_ai_client()
    if not client:
        raise RuntimeError('AI is not configured (AI_API_KEY missing)')

    kwargs = {
        'model': model or settings.AI_MODEL,
        'messages': messages,
        'max_tokens': max_tokens or settings.AI_MAX_TOKENS,
    }
    if tools:
        kwargs['tools'] = tools
        kwargs['tool_choice'] = 'auto'

    response = client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    content = _strip_thinking(choice.message.content or '')
    return {
        'content': content,
        'tool_calls': choice.message.tool_calls,
        'message': choice.message,
        'model': response.model,
        'prompt_tokens': response.usage.prompt_tokens if response.usage else None,
        'completion_tokens': response.usage.completion_tokens if response.usage else None,
    }


def _track_tool_usage(tool_call, tool_result, used_tools):
    """Extract a human-readable detail from a successful tool call."""
    from workspace.ai.tool_registry import tool_registry
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, AttributeError):
        args = {}
    used_tools.append((name, tool_registry.get_detail(name, args)))


def _render_tool_badges(used_tools):
    """Render HTML badges for tools used during response generation."""
    from workspace.ai.tool_registry import tool_registry

    grouped = {}
    for name, detail in used_tools:
        grouped.setdefault(name, [])
        if detail:
            grouped[name].append(detail)

    parts = []
    for name, details in grouped.items():
        cfg = tool_registry.get_badge(name)
        icon = cfg['icon']
        label = cfg['label']
        if details:
            details_display = ' &bull; '.join(details)
            parts.append(f'<span>{icon}</span> {label}: {details_display}')
        else:
            parts.append(f'<span>{icon}</span> {label}')

    # Single tool or short badges: inline. Multiple tools: one per line.
    if len(parts) <= 2:
        badges_html = ' <span class="opacity-30">|</span> '.join(parts)
        return (
            f'\n<div class="mt-2 text-xs text-base-content/40 flex items-center gap-1 flex-wrap">'
            f'{badges_html}'
            f'</div>'
        )

    badges_html = ''.join(
        f'<div class="flex items-center gap-1">{p}</div>' for p in parts
    )
    return (
        f'\n<div class="mt-2 text-xs text-base-content/40 flex flex-col gap-0.5">'
        f'{badges_html}'
        f'</div>'
    )


def _run_tool_loop(messages, model, human_user, bot_user, conversation_id):
    """Run the tool call loop and return (result, used_tools, tool_context, rounds, tool_data).

    Calls the AI model, executes any tool calls it returns, and re-calls
    until we get a plain text response (max 5 rounds).  *rounds* is a list
    of dicts capturing each LLM response and the tool executions that
    followed it, suitable for storage in ``AITask.raw_messages``.

    *tool_data* is a compact list of rounds suitable for persisting on
    ``Message.tool_data`` so that future history rebuilds can reconstruct
    the correct ``assistant(tool_calls) → tool(result)`` message sequence.
    """
    from workspace.ai.tool_registry import tool_registry

    tools = tool_registry.get_definitions()
    result = _call_llm(messages, model=model, tools=tools)

    used_tools = []
    tool_context = {}
    rounds = []
    tool_data = []  # compact history for Message.tool_data
    max_tool_rounds = 5
    for _ in range(max_tool_rounds):
        # Fallback: parse tool calls from text if model didn't use native function calling
        if not result.get('tool_calls') and result.get('content'):
            raw_calls, remaining = _extract_text_tool_calls(result['content'])
            if raw_calls:
                import types
                import uuid as _uuid
                result['content'] = remaining
                result['tool_calls'] = []
                for name, args_json in raw_calls:
                    call_id = f'call_{_uuid.uuid4().hex[:24]}'
                    tc = types.SimpleNamespace(
                        id=call_id,
                        type='function',
                        function=types.SimpleNamespace(name=name, arguments=args_json),
                    )
                    result['tool_calls'].append(tc)
                result['message'] = types.SimpleNamespace(
                    content=remaining or None,
                    tool_calls=result['tool_calls'],
                    role='assistant',
                )

        if not result.get('tool_calls'):
            rounds.append({'response': _serialize_response(result)})
            break

        round_data = {
            'response': _serialize_response(result),
            'tool_executions': [],
        }

        # Build tool_calls list for both the API message and tool_data persistence
        msg = result['message']
        tc_list = [
            {
                'id': tc.id,
                'type': 'function',
                'function': {'name': tc.function.name, 'arguments': tc.function.arguments},
            }
            for tc in msg.tool_calls
        ] if msg.tool_calls else []

        msg_dict = {'role': 'assistant', 'content': msg.content or ''}
        if tc_list:
            msg_dict['tool_calls'] = tc_list
        messages.append(msg_dict)

        td_round = {
            'assistant_content': msg.content or '',
            'tool_calls': tc_list,
            'results': [],
        }

        for tc in result['tool_calls']:
            tool_result = tool_registry.execute(
                tc, user=human_user, bot=bot_user,
                conversation_id=conversation_id, context=tool_context,
            )
            tool_content = _build_tool_content(tool_result)
            messages.append({
                'role': 'tool',
                'tool_call_id': tc.id,
                'content': tool_content,
            })
            if 'Error' not in tool_result and 'Unknown tool' not in tool_result:
                _track_tool_usage(tc, tool_result, used_tools)
            round_data['tool_executions'].append({
                'tool_call_id': tc.id,
                'name': tc.function.name,
                'arguments': tc.function.arguments,
                'result': _truncate_tool_result(tool_result),
            })
            # Store a text-only version for history reconstruction
            td_result_content = tool_result
            if isinstance(tool_content, list):
                # Multi-part content (e.g. image) — keep only the text part
                td_result_content = next(
                    (p['text'] for p in tool_content if isinstance(p, dict) and p.get('type') == 'text'),
                    tool_result,
                )
            td_round['results'].append({
                'tool_call_id': tc.id,
                'content': _truncate_tool_result(td_result_content),
            })

        tool_data.append(td_round)
        rounds.append(round_data)
        result = _call_llm(messages, model=model, tools=tools)
    else:
        # Max rounds reached — capture the final response
        rounds.append({'response': _serialize_response(result)})

    return result, used_tools, tool_context, rounds, tool_data or None


@transaction.atomic
def _post_bot_message(conversation, bot_user, result, used_tools, tool_context, ai_task, raw_messages=None, tool_data=None):
    """Create the bot message, attach images, update unread counts, notify, and complete AITask.

    Returns (body, bot_message).
    """
    from django.core.files.base import ContentFile
    from django.db.models import F

    from workspace.chat.models import ConversationMember, Message, MessageAttachment
    from workspace.chat.services.notifications import notify_conversation_members
    from workspace.chat.services.rendering import render_message_body

    body = _clean_llm_content(result['content'])
    body_html = render_message_body(body)

    if used_tools:
        body_html += _render_tool_badges(used_tools)

    bot_message = Message.objects.create(
        conversation_id=conversation.pk,
        author=bot_user,
        body=body,
        body_html=body_html,
        tool_data=tool_data,
    )

    # Attach any images generated by tools during this response
    pending = tool_context.get('images', [])
    for i, img in enumerate(pending):
        slug = img.get('prompt', 'image')[:60].strip().replace(' ', '_')
        slug = ''.join(c for c in slug if c.isalnum() or c in '_-')
        suffix = f'_{i + 1}' if len(pending) > 1 else ''
        filename = f'{slug}{suffix}.png'
        att = MessageAttachment(
            message=bot_message,
            original_name=filename,
            mime_type='image/png',
            size=len(img['data']),
        )
        att.file.save(filename, ContentFile(img['data']), save=False)
        att.save()

    ConversationMember.objects.filter(
        conversation_id=conversation.pk,
        left_at__isnull=True,
    ).exclude(user=bot_user).update(
        unread_count=F('unread_count') + 1,
    )

    from workspace.chat.models import Conversation as Conv
    Conv.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())

    notify_conversation_members(conversation, exclude_user=bot_user)

    ai_task.status = ai_task.Status.COMPLETED
    ai_task.result = body
    ai_task.chat_message = bot_message
    ai_task.model_used = result['model']
    ai_task.prompt_tokens = result['prompt_tokens']
    ai_task.completion_tokens = result['completion_tokens']
    ai_task.raw_messages = raw_messages
    ai_task.completed_at = timezone.now()
    ai_task.save()

    return body, bot_message


@transaction.atomic
def _handle_generation_error(conversation, bot_user, ai_task, error):
    """Handle a failed bot response: post error message, update counts, notify."""
    from django.db.models import F

    from workspace.chat.models import ConversationMember, Message
    from workspace.chat.services.notifications import notify_conversation_members
    from workspace.chat.services.rendering import render_message_body

    ai_task.status = ai_task.Status.FAILED
    ai_task.error = str(error)
    ai_task.completed_at = timezone.now()
    ai_task.save()

    error_body = f"⚠️ Sorry, I encountered an error: {error}"
    error_html = render_message_body(error_body)
    Message.objects.create(
        conversation_id=conversation.pk,
        author=bot_user,
        body=error_body,
        body_html=error_html,
    )
    ConversationMember.objects.filter(
        conversation_id=conversation.pk,
        left_at__isnull=True,
    ).exclude(user=bot_user).update(
        unread_count=F('unread_count') + 1,
    )
    from workspace.chat.models import Conversation as Conv
    Conv.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
    notify_conversation_members(conversation, exclude_user=bot_user)


def _build_conversation_history(conversation_id, bot_profile, human_user):
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
                    frames, desc = _extract_video_frames(att)
                    if desc:
                        video_descriptions.append(desc)
                    media_parts.extend(frames)

        if video_descriptions:
            history.append({'role': 'system', 'content': '\n'.join(video_descriptions)})

        if media_parts:
            content = []
            if body:
                content.append({'type': 'text', 'text': body})
            content.extend(media_parts)
            history.append({'role': role, 'content': content})
        else:
            history.append({'role': role, 'content': body})

    return history, summary_text


@shared_task(name='ai.generate_chat_response', bind=True, max_retries=0)
def generate_chat_response(self, conversation_id: str, message_id: str, bot_user_id: int):
    """Generate a bot response in a chat conversation."""
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AITask, BotProfile
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation, Message

    User = get_user_model()

    try:
        bot_user = User.objects.get(pk=bot_user_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=conversation_id)
    except (User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist):
        logger.error('Bot response failed: conversation=%s bot=%s not found', conversation_id, bot_user_id)
        return {'status': 'error', 'error': 'Not found'}

    trigger_message = Message.objects.filter(pk=message_id).select_related('author').first()
    human_user = trigger_message.author if trigger_message else None

    history, summary_text = _build_conversation_history(
        conversation_id, bot_profile, human_user,
    )

    bot_name = bot_user.get_full_name() or bot_user.username

    messages = build_chat_messages(
        bot_profile.system_prompt, history, bot_name=bot_name,
        user=human_user, bot=bot_user, summary=summary_text,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'conversation_id': conversation_id, 'message_id': message_id},
    )

    try:
        initial_messages = _sanitize_messages_for_storage(list(messages))

        result, used_tools, tool_context, rounds, tool_data = _run_tool_loop(
            messages, bot_profile.get_model(),
            human_user, bot_user, conversation_id,
        )

        # Auto-retry once if the model returned an empty response
        body_preview = _clean_llm_content(result.get('content') or '')
        if not body_preview and not tool_context.get('images'):
            logger.warning('Empty response, retrying once: conversation=%s', conversation_id)
            result, used_tools, tool_context, retry_rounds, retry_td = _run_tool_loop(
                messages, bot_profile.get_model(),
                human_user, bot_user, conversation_id,
            )
            rounds.extend(retry_rounds)
            if retry_td:
                tool_data = (tool_data or []) + retry_td
            body_preview = _clean_llm_content(result.get('content') or '')
            if not body_preview and not tool_context.get('images'):
                raise RuntimeError('Empty response from model')

        raw_messages = {'messages': initial_messages, 'rounds': rounds}

        # Guard: check if the task was cancelled while we were waiting for OpenAI
        ai_task.refresh_from_db(fields=['status'])
        if ai_task.status == AITask.Status.FAILED:
            logger.info('Bot response cancelled: conversation=%s', conversation_id)
            return {'status': 'cancelled'}

        body, bot_message = _post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
            raw_messages, tool_data=tool_data,
        )

        # Auto-generate title if the conversation doesn't have one yet
        msg_count = Message.objects.filter(
            conversation_id=conversation_id, deleted_at__isnull=True,
        ).count()
        if not conversation.title and msg_count >= 2:
            generate_conversation_title.delay(str(conversation_id))

        # Trigger rolling summary update when old messages exceed the recent window
        _recent = settings.AI_CHAT_CONTEXT_SIZE
        if msg_count > _recent:
            from workspace.ai.models import ConversationSummary
            _cs = ConversationSummary.objects.filter(conversation_id=conversation_id).first()
            needs_summary = not summary_text
            if not needs_summary and _cs and _cs.up_to:
                unsummarized = Message.objects.filter(
                    conversation_id=conversation_id,
                    deleted_at__isnull=True,
                    created_at__gt=_cs.up_to,
                ).count()
                needs_summary = unsummarized > _recent + _SUMMARY_BUFFER
            if needs_summary:
                update_conversation_summary.delay(str(conversation_id))

        logger.info('Bot response generated: conversation=%s tokens=%s+%s',
                     conversation_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Bot response failed: conversation=%s', conversation_id)
        _handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.update_conversation_summary', bind=True, max_retries=0)
def update_conversation_summary(self, conversation_id: str):
    """Update the rolling summary for a bot conversation.

    Summarises messages that fall outside the recent window using the small
    model, then stores the result on the ``ConversationSummary`` so future
    responses can include the condensed context instead of raw old messages.
    """
    from workspace.ai.models import ConversationSummary
    from workspace.chat.models import Conversation, Message

    if not Conversation.objects.filter(pk=conversation_id).exists():
        return {'status': 'error', 'error': 'Conversation not found'}

    recent_window = settings.AI_CHAT_CONTEXT_SIZE

    msg_qs = Message.objects.filter(
        conversation_id=conversation_id,
        deleted_at__isnull=True,
    )
    total = msg_qs.count()
    if total <= recent_window:
        return {'status': 'skipped', 'reason': 'not enough messages'}

    # The cutoff is the newest message outside the recent window (i.e. the
    # first one that should be summarised rather than kept verbatim).
    cutoff_msg = (
        msg_qs.order_by('-created_at')
        .values_list('created_at', flat=True)[recent_window:recent_window + 1]
    )
    cutoff_time = list(cutoff_msg)[0] if cutoff_msg else None
    if not cutoff_time:
        return {'status': 'skipped', 'reason': 'could not determine cutoff'}

    conv_summary = ConversationSummary.objects.filter(conversation_id=conversation_id).first()

    # Already up-to-date?
    if conv_summary and conv_summary.up_to and conv_summary.up_to >= cutoff_time:
        return {'status': 'skipped', 'reason': 'already up to date'}

    # Only fetch messages that need summarising (after last summary, up to cutoff)
    new_qs = msg_qs.filter(created_at__lte=cutoff_time).order_by('created_at')
    if conv_summary and conv_summary.up_to:
        new_qs = new_qs.filter(created_at__gt=conv_summary.up_to)

    new_messages = list(
        new_qs.select_related('author', 'author__bot_profile')
    )
    if not new_messages:
        return {'status': 'skipped', 'reason': 'no new messages to summarize'}

    # Format messages — truncate individually to keep the summarisation prompt lean
    lines = []
    for msg in new_messages:
        name = msg.author.get_full_name() or msg.author.username
        is_bot = hasattr(msg.author, 'bot_profile')
        label = f'[Bot] {name}' if is_bot else name
        body = msg.body[:1000] if len(msg.body) > 1000 else msg.body
        lines.append(f'{label}: {body}')

    messages_text = '\n'.join(lines)

    system = (
        'Summarize this conversation concisely. Preserve:\n'
        '- Key topics discussed and conclusions reached\n'
        '- User preferences, personal details, and requests\n'
        '- Ongoing tasks or commitments\n'
        '- Important context needed to continue the conversation naturally\n\n'
        'Write in the same language as the conversation. Be concise but complete.'
    )

    existing = conv_summary.content if conv_summary else ''
    if existing:
        user_content = f'Previous summary:\n{existing}\n\nNew messages to incorporate:\n{messages_text}'
    else:
        user_content = f'Messages:\n{messages_text}'

    prompt_messages = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user_content},
    ]

    try:
        result = _call_llm(
            prompt_messages,
            model=settings.AI_SMALL_MODEL or settings.AI_MODEL,
            max_tokens=4096,
        )
        summary_content = result['content']
        if not summary_content:
            logger.warning(
                'Empty summary from model (tokens=%s+%s), skipping: conversation=%s',
                result.get('prompt_tokens'), result.get('completion_tokens'),
                conversation_id,
            )
            return {'status': 'error', 'error': 'Empty summary from model'}

        ConversationSummary.objects.update_or_create(
            conversation_id=conversation_id,
            defaults={'content': summary_content, 'up_to': cutoff_time},
        )

        logger.info(
            'Conversation summary updated: conversation=%s messages_summarized=%d tokens=%s+%s',
            conversation_id, len(new_messages),
            result['prompt_tokens'], result['completion_tokens'],
        )
        return {'status': 'ok'}

    except Exception as e:
        logger.exception('Conversation summary failed: conversation=%s', conversation_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.summarize', bind=True, max_retries=0)
def summarize(self, task_id: str):
    """Summarize a mail message."""
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_summarize_messages
    from workspace.core.sse_registry import notify_sse
    from workspace.mail.models import MailMessage

    try:
        ai_task = AITask.objects.get(pk=task_id)
    except AITask.DoesNotExist:
        logger.error('Summarize task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    try:
        message = MailMessage.objects.get(
            pk=ai_task.input_data['message_id'],
            account__owner=ai_task.owner,
        )
    except MailMessage.DoesNotExist:
        ai_task.status = AITask.Status.FAILED
        ai_task.error = 'Mail message not found'
        ai_task.completed_at = timezone.now()
        ai_task.save()
        return {'status': 'error', 'error': 'Mail message not found'}

    body = message.body_text or message.body_html or ''
    messages = build_summarize_messages(message.subject or '', body)

    try:
        result = _call_llm(messages, model=settings.AI_SMALL_MODEL)
        with transaction.atomic():
            ai_task.status = AITask.Status.COMPLETED
            ai_task.result = result['content']
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.raw_messages = {
                'messages': _sanitize_messages_for_storage(messages),
                'response': _serialize_response(result),
            }
            ai_task.completed_at = timezone.now()
            ai_task.save()

            message.ai_summary = result['content']
            message.save(update_fields=['ai_summary'])

        notify_sse('ai', ai_task.owner_id)

        logger.info('Summarize complete: task=%s tokens=%s+%s',
                     task_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Summarize failed: task=%s', task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.editor_action', bind=True, max_retries=0)
def editor_action(self, task_id: str):
    """Run an AI action on editor content (improve, explain, summarize, custom)."""
    from workspace.ai.models import AITask
    from workspace.ai.prompts.editor import (
        build_custom_messages,
        build_explain_messages,
        build_improve_messages,
        build_summarize_messages,
    )
    from workspace.core.sse_registry import notify_sse

    try:
        ai_task = AITask.objects.get(pk=task_id)
    except AITask.DoesNotExist:
        logger.error('Editor action task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    action = ai_task.input_data.get('action', '')
    content = ai_task.input_data.get('content', '')
    language = ai_task.input_data.get('language', '')
    filename = ai_task.input_data.get('filename', '')

    builders = {
        'improve': lambda: build_improve_messages(content, language, filename),
        'explain': lambda: build_explain_messages(content, language, filename),
        'summarize': lambda: build_summarize_messages(content, language, filename),
        'custom': lambda: build_custom_messages(
            content, ai_task.input_data.get('instructions', ''), language, filename,
        ),
    }

    builder = builders.get(action)
    if not builder:
        ai_task.status = AITask.Status.FAILED
        ai_task.error = f'Unknown action: {action}'
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': f'Unknown action: {action}'}

    try:
        messages = builder()
        result = _call_llm(messages)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
        ai_task.raw_messages = {
            'messages': _sanitize_messages_for_storage(messages),
            'response': _serialize_response(result),
        }
        ai_task.completed_at = timezone.now()
        ai_task.save()

        notify_sse('ai', ai_task.owner_id)

        logger.info('Editor action complete: task=%s action=%s tokens=%s+%s',
                     task_id, action, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Editor action failed: task=%s action=%s', task_id, action)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.compose_email', bind=True, max_retries=0)
def compose_email(self, task_id: str):
    """Compose or reply to an email."""
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_compose_messages, build_reply_messages
    from workspace.core.sse_registry import notify_sse
    from workspace.mail.models import MailAccount, MailMessage

    try:
        ai_task = AITask.objects.get(pk=task_id, owner__isnull=False)
    except AITask.DoesNotExist:
        logger.error('Compose task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    instructions = ai_task.input_data.get('instructions', '')
    original_message_id = ai_task.input_data.get('message_id')

    # Resolve sender identity from the mail account or user profile
    sender_name = ''
    sender_email = ''
    account_id = ai_task.input_data.get('account_id')
    if account_id:
        account = MailAccount.objects.filter(pk=account_id, owner=ai_task.owner).first()
        if account:
            sender_name = account.display_name
            sender_email = account.email
    if not sender_email:
        sender_name = ai_task.owner.get_full_name()
        sender_email = ai_task.owner.email or ''

    try:
        if original_message_id:
            message = MailMessage.objects.select_related('account').get(
                pk=original_message_id,
                account__owner=ai_task.owner,
            )
            body = message.body_text or message.body_html or ''
            # Use the account from the original message for reply
            reply_name = message.account.display_name or sender_name
            reply_email = message.account.email or sender_email
            messages = build_reply_messages(
                instructions, message.subject or '', body,
                sender_name=reply_name, sender_email=reply_email,
            )
        else:
            context = ai_task.input_data.get('context', '')
            messages = build_compose_messages(
                instructions, context,
                sender_name=sender_name, sender_email=sender_email,
            )

        result = _call_llm(messages)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
        ai_task.raw_messages = {
            'messages': _sanitize_messages_for_storage(messages),
            'response': _serialize_response(result),
        }
        ai_task.completed_at = timezone.now()
        ai_task.save()

        notify_sse('ai', ai_task.owner_id)

        logger.info('Compose complete: task=%s tokens=%s+%s',
                     task_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Compose failed: task=%s', task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.generate_conversation_title', bind=True, max_retries=0)
def generate_conversation_title(self, conversation_id: str):
    """Generate a short title for a bot conversation based on the first exchange."""
    from workspace.chat.models import Conversation, Message
    from workspace.chat.services.notifications import notify_conversation_members

    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return {'status': 'error', 'error': 'Conversation not found'}

    # Only generate if no title set yet
    if conversation.title:
        return {'status': 'skipped', 'reason': 'already has title'}

    # Grab the first few messages for context
    messages = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        ).order_by('created_at').values_list('body', flat=True)[:6]
    )
    if not messages:
        return {'status': 'skipped', 'reason': 'no messages'}

    excerpt = '\n'.join(m for m in messages if m)

    try:
        result = _call_llm(
            [
                {
                    'role': 'system',
                    'content': (
                        'Generate a short title (max 6 words) for this conversation. '
                        'Reply with ONLY the title, no quotes, no punctuation at the end.'
                    ),
                },
                {'role': 'user', 'content': excerpt},
            ],
            model=settings.AI_SMALL_MODEL,
            max_tokens=2048,
        )
        title = result['content'].strip().strip('"\'')
        if title:
            conversation.title = title[:255]
            conversation.save(update_fields=['title'])
            notify_conversation_members(conversation)
        return {'status': 'ok', 'title': title}
    except Exception as e:
        logger.exception('Title generation failed: conversation=%s', conversation_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.purge_ai_tasks', bind=True, max_retries=0)
def purge_ai_tasks(self):
    """Delete completed AI tasks older than AI_TASK_RETENTION_DAYS."""
    from datetime import timedelta

    from workspace.ai.models import AITask

    retention_days = getattr(settings, 'AI_TASK_RETENTION_DAYS', 90)
    cutoff = timezone.now() - timedelta(days=retention_days)

    qs = AITask.objects.filter(created_at__lte=cutoff)
    count = qs.count()

    if not count:
        logger.info('AI task purge: nothing to delete.')
        return {'deleted': 0, 'retention_days': retention_days}

    logger.info('AI task purge: deleting %d tasks older than %d days', count, retention_days)
    qs.delete()

    logger.info('AI task purge complete.')
    return {'deleted': count, 'retention_days': retention_days}


@shared_task(name='ai.dispatch_scheduled_messages')
def dispatch_scheduled_messages():
    """Find due scheduled messages and dispatch a generation task for each."""
    from workspace.ai.models import ScheduledMessage

    now = timezone.now()
    due = ScheduledMessage.objects.filter(is_active=True, next_run_at__lte=now)
    count = 0
    for schedule in due:
        generate_scheduled_response.delay(str(schedule.uuid))
        count += 1
    if count:
        logger.info('Dispatched %d scheduled message(s)', count)


@shared_task(name='ai.generate_scheduled_response', bind=True, max_retries=0)
def generate_scheduled_response(self, schedule_id: str):
    """Generate a bot response for a scheduled message."""
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AITask, BotProfile, ScheduledMessage
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation, Message
    from workspace.chat.services.notifications import notify_new_message

    User = get_user_model()

    # Load the schedule and advance it immediately to prevent duplicate dispatches
    try:
        schedule = ScheduledMessage.objects.get(pk=schedule_id)
    except ScheduledMessage.DoesNotExist:
        logger.error('Scheduled message not found: %s', schedule_id)
        return {'status': 'error', 'error': 'Schedule not found'}

    if not schedule.is_active:
        return {'status': 'skipped', 'reason': 'inactive'}

    from workspace.users.services.settings import get_user_timezone
    creator_tz = get_user_timezone(schedule.created_by)

    schedule.last_run_at = timezone.now()
    schedule.compute_next_run(user_tz=creator_tz)
    schedule.save(update_fields=['last_run_at', 'next_run_at', 'is_active'])

    try:
        bot_user = User.objects.get(pk=schedule.bot_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=schedule.conversation_id)
    except (User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist):
        logger.error('Scheduled response failed: schedule=%s — bot or conversation not found', schedule_id)
        return {'status': 'error', 'error': 'Not found'}

    human_user = User.objects.filter(pk=schedule.created_by_id).first()

    history, summary_text = _build_conversation_history(
        str(conversation.pk), bot_profile, human_user,
    )

    bot_name = bot_user.get_full_name() or bot_user.username

    # Inject the scheduled action instruction into the system prompt
    scheduled_instruction = (
        f'\n\n## Scheduled action\n'
        f'You previously scheduled a proactive message with the following instruction:\n'
        f'"{schedule.prompt}"\n\n'
        f'Now is the time to act on it. Generate an appropriate message for the user.\n'
        f'Be natural — do not mention that you are a scheduled message.\n'
        f'If, based on the conversation context, you judge that this message is no longer '
        f'relevant or useful (e.g. the topic was already addressed, the event has passed, '
        f'the user already handled it), reply with exactly "[SKIP]" and nothing else.'
    )

    messages = build_chat_messages(
        bot_profile.system_prompt + scheduled_instruction,
        history, bot_name=bot_name,
        user=human_user, bot=bot_user, summary=summary_text,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'schedule_id': schedule_id, 'conversation_id': str(conversation.pk)},
    )

    try:
        initial_messages = _sanitize_messages_for_storage(list(messages))

        result, used_tools, tool_context, rounds, tool_data = _run_tool_loop(
            messages, bot_profile.get_model(),
            human_user, bot_user, str(conversation.pk),
        )

        # Auto-retry once if the model returned an empty response
        body_preview = _clean_llm_content(result.get('content') or '')
        if not body_preview and not tool_context.get('images'):
            logger.warning('Empty scheduled response, retrying once: schedule=%s', schedule_id)
            result, used_tools, tool_context, retry_rounds, retry_td = _run_tool_loop(
                messages, bot_profile.get_model(),
                human_user, bot_user, str(conversation.pk),
            )
            rounds.extend(retry_rounds)
            if retry_td:
                tool_data = (tool_data or []) + retry_td
            body_preview = _clean_llm_content(result.get('content') or '')
            if not body_preview and not tool_context.get('images'):
                ai_task.status = ai_task.Status.COMPLETED
                ai_task.result = '[EMPTY]'
                ai_task.model_used = result.get('model', '')
                ai_task.prompt_tokens = result.get('prompt_tokens')
                ai_task.completion_tokens = result.get('completion_tokens')
                ai_task.completed_at = timezone.now()
                ai_task.save()
                logger.warning('Scheduled response empty after retry: schedule=%s', schedule_id)
                return {'status': 'skipped', 'reason': 'empty_response'}

        raw_messages = {'messages': initial_messages, 'rounds': rounds}

        # Let the bot skip if it judges the message is no longer relevant
        body = _clean_llm_content(result['content'])
        if body == '[SKIP]':
            ai_task.status = ai_task.Status.COMPLETED
            ai_task.result = '[SKIP]'
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.raw_messages = raw_messages
            ai_task.completed_at = timezone.now()
            ai_task.save()
            logger.info('Scheduled response skipped (bot judged irrelevant): schedule=%s', schedule_id)
            return {'status': 'skipped', 'reason': 'bot_judged_irrelevant'}

        body, bot_message = _post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
            raw_messages, tool_data=tool_data,
        )

        notify_new_message(conversation, bot_user, body)

        # Trigger rolling summary update if needed
        _recent = settings.AI_CHAT_CONTEXT_SIZE
        msg_count = Message.objects.filter(
            conversation_id=conversation.pk, deleted_at__isnull=True,
        ).count()
        if msg_count > _recent:
            from workspace.ai.models import ConversationSummary
            _cs = ConversationSummary.objects.filter(conversation_id=conversation.pk).first()
            needs_summary = not summary_text
            if not needs_summary and _cs and _cs.up_to:
                unsummarized = Message.objects.filter(
                    conversation_id=conversation.pk,
                    deleted_at__isnull=True,
                    created_at__gt=_cs.up_to,
                ).count()
                needs_summary = unsummarized > _recent + _SUMMARY_BUFFER
            if needs_summary:
                update_conversation_summary.delay(str(conversation.pk))

        logger.info('Scheduled response generated: schedule=%s conversation=%s tokens=%s+%s',
                     schedule_id, conversation.pk, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Scheduled response failed: schedule=%s', schedule_id)
        _handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}


CLASSIFY_BATCH_SIZE = 10
MAX_LABELS_PER_MESSAGE = 3


@shared_task(name='ai.classify_mail', bind=True, max_retries=0)
def classify_mail_messages(self, task_id: str):
    """Classify mail messages by assigning user-defined labels."""
    import orjson as _orjson
    from collections import defaultdict
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_classify_messages
    from workspace.core.sse_registry import notify_sse
    from workspace.mail.models import MailLabel, MailMessage, MailMessageLabel

    try:
        ai_task = AITask.objects.get(pk=task_id)
    except AITask.DoesNotExist:
        logger.error('Classify task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    message_uuids = ai_task.input_data.get('message_uuids', [])
    msgs = list(
        MailMessage.objects.filter(
            uuid__in=message_uuids,
            account__owner=ai_task.owner,
        ).only('uuid', 'subject', 'from_address', 'snippet', 'account_id')
    )

    if not msgs:
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = 'No messages to classify'
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'ok', 'task_id': task_id}

    # Group messages by account
    msgs_by_account = defaultdict(list)
    for m in msgs:
        msgs_by_account[m.account_id].append(m)

    total_prompt = 0
    total_completion = 0
    model_used = ''

    try:
        for account_id, account_msgs in msgs_by_account.items():
            account_labels = list(MailLabel.objects.filter(account_id=account_id))
            label_names = [lbl.name for lbl in account_labels]
            label_by_lower = {lbl.name.lower(): lbl for lbl in account_labels}

            for batch_start in range(0, len(account_msgs), CLASSIFY_BATCH_SIZE):
                batch = account_msgs[batch_start:batch_start + CLASSIFY_BATCH_SIZE]
                uuid_index = {i + 1: m for i, m in enumerate(batch)}

                emails = []
                for m in batch:
                    from_addr = m.from_address if isinstance(m.from_address, dict) else {}
                    emails.append({
                        'subject': m.subject or '',
                        'from_name': from_addr.get('name', ''),
                        'from_email': from_addr.get('email', ''),
                        'snippet': m.snippet or '',
                    })

                messages = build_classify_messages(emails, label_names)
                result = _call_llm(messages, model=settings.AI_SMALL_MODEL)

                model_used = result['model']
                total_prompt += result['prompt_tokens'] or 0
                total_completion += result['completion_tokens'] or 0

                try:
                    items = _orjson.loads(result['content'])
                except (ValueError, TypeError):
                    logger.warning('Classify: malformed JSON response for task %s', task_id)
                    raise ValueError('Malformed JSON response from LLM')

                if not isinstance(items, list):
                    raise ValueError('Expected JSON array from LLM')

                links_to_create = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    idx = item.get('i')
                    raw_labels = item.get('labels', [])

                    msg = uuid_index.get(idx)
                    if not msg:
                        continue

                    if not isinstance(raw_labels, list):
                        continue

                    count = 0
                    for raw_name in raw_labels:
                        if count >= MAX_LABELS_PER_MESSAGE:
                            break
                        if not isinstance(raw_name, str):
                            continue
                        label = label_by_lower.get(raw_name.lower())
                        if label:
                            links_to_create.append(
                                MailMessageLabel(message=msg, label=label)
                            )
                            count += 1

                if links_to_create:
                    MailMessageLabel.objects.bulk_create(links_to_create, ignore_conflicts=True)

        with transaction.atomic():
            ai_task.status = AITask.Status.COMPLETED
            ai_task.result = f'Classified {len(msgs)} messages'
            ai_task.model_used = model_used
            ai_task.prompt_tokens = total_prompt
            ai_task.completion_tokens = total_completion
            ai_task.completed_at = timezone.now()
            ai_task.save()
        notify_sse('ai', ai_task.owner_id)

        logger.info('Classify complete: task=%s messages=%d tokens=%d+%d',
                     task_id, len(msgs), total_prompt, total_completion)
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Classify failed: task=%s', task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}
