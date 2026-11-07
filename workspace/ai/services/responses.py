import logging

from django.db import transaction

from workspace.ai.harness.model import RunUsage
from workspace.ai.models import AITask
from workspace.ai.services.ai_task import close_task
from workspace.ai.services.llm import clean_llm_content
from workspace.ai.services.speech import audio_duration_seconds
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def _describe_media(data, group, fallback):
    """Return (label, mime_type, category, extension) for bytes a tool produced.

    A media backend returns whatever format its model produces -- PNG, JPEG or
    WebP for an image, WAV for speech -- regardless of what was requested, so
    the type is read from the bytes instead of assumed. Bytes Magika cannot
    place in *group* keep *fallback* rather than being stored as an opaque
    blob, which would drop the attachment out of the media gallery and out of
    the viewer that makes it playable.
    """
    from workspace.files.services.detection import detect_from_bytes, get_label_info

    detection = detect_from_bytes(data)
    if detection.group != group:
        return fallback
    default_extension = fallback[3].removeprefix(".")
    extensions = get_label_info(detection.label).get("extensions") or [
        default_extension
    ]
    return detection.label, detection.mime_type, detection.group, f".{extensions[0]}"


def _describe_image(data):
    return _describe_media(data, "image", ("unknown", "image/png", "unknown", ".png"))


def _describe_audio(data):
    return _describe_media(data, "audio", ("wav", "audio/wav", "audio", ".wav"))


def produced_media(tool_context) -> bool:
    """Whether tools left media for the reply to carry.

    A reply that is only an image or only a voice message has an empty body
    on purpose, and must not be mistaken for the model answering nothing.
    """
    return bool(tool_context.get("images") or tool_context.get("voices"))


def run_usage_fields(response, usage):
    """What a run cost, as the AITask columns that record it.

    *usage* is the total of every call; *response* the call the run ended on,
    whose own figures give the speed at which the answer itself was written.
    """
    return {
        "model_used": response.model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "generation_seconds": usage.seconds,
        "answer_tokens": response.completion_tokens,
        "answer_seconds": response.duration,
    }


@transaction.atomic
def post_bot_message(
    conversation,
    bot_user,
    response,
    tool_context,
    ai_task,
    raw_messages=None,
    tool_data=None,
    usage=None,
):
    """Complete AITask, then create the bot message, attach its media, update unread counts and notify.

    *response* is the :class:`~workspace.ai.harness.model.ModelResponse` the
    run ended on and *usage* the :class:`~workspace.ai.harness.model.RunUsage`
    of the whole run; a single-call reply passes no usage and the response is
    the whole run. Returns (body, bot_message), or None when the task was
    closed before the reply could land - the user cancelled it - in which
    case nothing is posted.
    """
    from django.core.files.base import ContentFile

    from workspace.chat.models import MessageAttachment, MessageInteraction
    from workspace.chat.services.posting import post_message
    from workspace.chat.services.rendering import render_message_body

    body = clean_llm_content(response.content)

    if usage is None:
        usage = RunUsage()
        usage.add(response)
    # Closed before the message exists, in the same transaction: the
    # conditional write holds the row until commit, so a cancel either lands
    # first and nothing is posted, or finds the task closed and changes nothing.
    if not close_task(
        ai_task,
        AITask.Status.COMPLETED,
        result=body,
        raw_messages=raw_messages,
        **run_usage_fields(response, usage),
    ):
        logger.info("Bot reply dropped, task already closed: task=%s", ai_task.pk)
        return None

    body_html = render_message_body(body)

    # Reasoning of the final completion is not captured in tool_data, which
    # only records rounds that executed tools. Append it as a tool-less
    # round so the UI timeline can show it. Skip when it duplicates the last
    # round's thinking: on the stop_after_round path the posted response IS
    # the last tool round.
    thinking = response.thinking.strip()
    if thinking and not (tool_data and tool_data[-1].get("thinking") == thinking):
        tool_data = [
            *(tool_data or []),
            {"thinking": thinking, "tool_calls": [], "results": []},
        ]

    bot_message = post_message(
        conversation,
        bot_user,
        body,
        body_html=body_html,
        tool_data=tool_data,
    )

    # If a tool wrote tool_context['question'] (currently only the
    # ask_user_question tool does this), persist it as an interactive UI
    # block attached to the bot message. The surrounding @transaction.atomic
    # decorator on post_bot_message guarantees both rows commit together.
    question = tool_context.get("question")
    if question:
        MessageInteraction.objects.create(
            message=bot_message,
            kind=MessageInteraction.Kind.QUESTION,
            payload=question,
        )

    # Attach any images generated by tools during this response, in the order
    # the model asked for them: a round generates its images in parallel, so
    # the order they were appended in is the order they came back in.
    pending = sorted(
        tool_context.get("images", []), key=lambda image: image.get("position", 0)
    )
    created_attachments = []
    for i, img in enumerate(pending):
        data = img["data"]
        label, mime_type, category, extension = _describe_image(data)
        slug = img.get("prompt", "image")[:60].strip().replace(" ", "_")
        slug = "".join(c for c in slug if c.isalnum() or c in "_-")
        suffix = f"_{i + 1}" if len(pending) > 1 else ""
        filename = f"{slug}{suffix}{extension}"
        att = MessageAttachment(
            message=bot_message,
            original_name=filename,
            mime_type=mime_type,
            type=label,
            category=category,
            size=len(data),
        )
        att.file.save(filename, ContentFile(data), save=False)
        att.save()
        created_attachments.append(att)

    # Voice messages recorded by tools during this response. The spoken text
    # is stored as the attachment's description: it is what later turns read
    # back instead of the audio, and the caption task never runs on audio.
    voices = sorted(
        tool_context.get("voices", []), key=lambda voice: voice.get("position", 0)
    )
    for i, voice in enumerate(voices):
        data = voice["data"]
        label, mime_type, category, extension = _describe_audio(data)
        suffix = f"_{i + 1}" if len(voices) > 1 else ""
        filename = f"voice_message{suffix}{extension}"
        att = MessageAttachment(
            message=bot_message,
            original_name=filename,
            mime_type=mime_type,
            type=label,
            category=category,
            size=len(data),
            duration_seconds=audio_duration_seconds(data),
            ai_description=voice.get("text", ""),
        )
        att.file.save(filename, ContentFile(data), save=False)
        att.save()
        created_attachments.append(att)

    if created_attachments:
        from workspace.ai.tasks.captions import enqueue_caption_if_image

        transaction.on_commit(
            lambda atts=created_attachments: [enqueue_caption_if_image(a) for a in atts]
        )

    ai_task.chat_message = bot_message
    ai_task.save(update_fields=["chat_message"])

    return body, bot_message


@transaction.atomic
def handle_generation_error(conversation, bot_user, ai_task, error):
    """Handle a failed bot response: post error message, update counts, notify.

    Posts nothing when the task was already closed: a user who cancelled the
    reply asked for silence, and keeps the cancel as the recorded reason.
    """
    from workspace.chat.services.posting import post_message
    from workspace.chat.services.rendering import render_message_body

    logger.warning(
        "Bot generation failed for conversation=%s: %s", conversation.pk, scrub(error)
    )
    # Detailed error stays on AITask + logs; the chat sees a generic message so
    # we don't leak provider/backend/user data into the conversation body.
    if not close_task(ai_task, AITask.Status.FAILED, error=str(error)):
        return

    error_body = (
        "⚠️ Sorry, I encountered an error generating a response. Please try again."
    )
    error_html = render_message_body(error_body)
    post_message(conversation, bot_user, error_body, body_html=error_html)
