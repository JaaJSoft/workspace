"""Core AI chat tools (memory, workspace search, avatar, image generation)."""

import base64
import binascii
import json
import logging
import uuid as uuid_lib
from datetime import UTC, datetime

from django.conf import settings
from pydantic import BaseModel, Field

from workspace.common.logging import scrub

from .client import get_image_client
from .models import UserMemory
from .tool_registry import ToolProvider, tool

logger = logging.getLogger(__name__)


class SaveMemoryParams(BaseModel):
    key: str = Field(
        description="A short category label (e.g. name, language, project, preference)."
    )
    content: str = Field(description="The fact to remember.")


class DeleteMemoryParams(BaseModel):
    key: str = Field(description="The key of the memory to delete.")


class WebSearchParams(BaseModel):
    query: str = Field(description="The search query.")


class ReadWebpageParams(BaseModel):
    url: str = Field(description="The URL of the webpage to read.")


class GetWeatherParams(BaseModel):
    location: str = Field(
        description="The place to get the weather for — a city, region, or country "
        "(e.g. 'Paris', 'Tokyo', 'New York')."
    )


class GenerateImageParams(BaseModel):
    prompt: str = Field(description="A detailed description of the image to generate.")
    size: str = Field(
        default="1024x1024",
        description="Image size: 1024x1024, 1792x1024, or 1024x1792.",
    )


class EditImageParams(BaseModel):
    prompt: str = Field(
        description="A description of the changes to apply to the image."
    )
    size: str = Field(
        default="1024x1024",
        description="Output size: 1024x1024, 1792x1024, or 1024x1792.",
    )


class ScheduleMessageParams(BaseModel):
    prompt: str = Field(description="The instruction/intent for the future message.")
    at: str = Field(
        default="",
        description="ISO datetime for one-time scheduling (e.g. 2026-03-10T09:00). Mutually exclusive with every/interval.",
    )
    every: str = Field(
        default="",
        description="Recurrence unit: hours, days, weeks, months. Mutually exclusive with at.",
    )
    interval: int = Field(default=1, description="Recurrence interval (default 1).")
    at_time: str = Field(
        default="",
        description="Time of day for daily/weekly/monthly recurrence (HH:MM, 24h format).",
    )
    on_day: int | None = Field(
        default=None,
        description="Day of week (0=Mon..6=Sun) for weekly, or day of month (1-31) for monthly.",
    )


class CancelScheduleParams(BaseModel):
    schedule_id: str = Field(description="UUID of the schedule to cancel.")


class CreateAgentGoalParams(BaseModel):
    title: str = Field(description="Short label for the goal (a few words).")
    goal: str = Field(
        description="The full objective: what to accomplish or track, what success "
        "looks like, and any context needed to work on it autonomously."
    )
    first_check_at: str = Field(
        description="ISO datetime of the first autonomous check-in "
        "(e.g. 2026-03-10T09:00), in the user's local timezone."
    )
    deadline: str = Field(
        default="",
        description="Optional ISO datetime by which the goal should be wrapped up.",
    )


class UpdateAgentGoalParams(BaseModel):
    goal_id: uuid_lib.UUID = Field(description="UUID of the goal to update.")
    notes: str = Field(
        default="",
        description="Replace your private working notes (plan, progress, findings). "
        "They are your only memory between check-ins — always rewrite them in full "
        "with everything you still need.",
    )
    next_check_at: str = Field(
        default="",
        description="ISO datetime of your next autonomous check-in, in the user's "
        "local timezone.",
    )
    deadline: str = Field(
        default="", description="New ISO datetime deadline for the goal."
    )
    status: str = Field(
        default="",
        description='Set to "paused" to pause the goal or "active" to resume it.',
    )


class CompleteAgentGoalParams(BaseModel):
    goal_id: uuid_lib.UUID = Field(description="UUID of the goal to close.")
    outcome: str = Field(
        description="Final summary of what was accomplished, or why the goal "
        "is being dropped."
    )
    abandoned: bool = Field(
        default=False,
        description="True if the goal is dropped without being achieved.",
    )


def _bot_supports_vision(bot):
    profile = getattr(bot, "bot_profile", None)
    return bool(profile and profile.supports_vision)


def _image_tool_payload(image_data, text):
    """Tool result that lets a vision bot see the image it just produced."""
    from workspace.files.services.detection import detect_from_bytes

    detection = detect_from_bytes(image_data)
    mime = detection.mime_type if detection.group == "image" else "image/png"
    return json.dumps(
        {
            "type": "image",
            "mime_type": mime,
            "data": base64.b64encode(image_data).decode(),
            "text": text,
        }
    )


class CoreToolProvider(ToolProvider):
    @tool(badge_icon="👤", badge_label="Looked up profile")
    def get_current_user_info(self, args, user, bot, conversation_id, context):
        """Get the profile of the user you are chatting with: username, full name, email, and join date. \
Call this when you need to address the user by name, check their email, or answer questions about their account."""
        if not user:
            return "Error: no user context"
        from workspace.users.services.settings import get_user_timezone

        joined = user.date_joined.astimezone(get_user_timezone(user))
        info = {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "date_joined": joined.strftime("%Y-%m-%d"),
        }
        return json.dumps(info)

    @tool(
        badge_icon="🧠",
        badge_label="Retained",
        detail_key="key",
        params=SaveMemoryParams,
    )
    def save_memory(self, args, user, bot, conversation_id, context):
        """Persistently save a fact about the user so you can recall it in future conversations. \
Call this proactively when the user tells you their name, preferences, projects, or any personal detail worth remembering. \
If the key already exists it will be updated."""
        key = args.key.strip()[:100]
        content = args.content.strip()
        if not key or not content:
            return "Error: key and content are required"
        UserMemory.objects.update_or_create(
            user=user,
            bot=bot,
            key=key,
            defaults={"content": content},
        )
        logger.info(
            "Memory saved: %s/%s - %s",
            scrub(user.username),
            scrub(bot.username),
            scrub(key),
        )
        return f'Saved memory "{key}".'

    @tool(
        badge_icon="🧠",
        badge_label="Forgot",
        detail_key="key",
        params=DeleteMemoryParams,
    )
    def delete_memory(self, args, user, bot, conversation_id, context):
        """Delete a previously saved memory. \
Call this when the user explicitly asks you to forget something or when a stored fact is no longer correct."""
        key = args.key.strip()
        deleted, _ = UserMemory.objects.filter(user=user, bot=bot, key=key).delete()
        if deleted:
            logger.info(
                "Memory deleted: %s/%s - %s",
                scrub(user.username),
                scrub(bot.username),
                scrub(key),
            )
            return f'Deleted memory "{key}".'
        return f'Memory "{key}" not found.'

    @tool(badge_icon="🖼️", badge_label="Viewed own avatar")
    def get_my_avatar(self, args, user, bot, conversation_id, context):
        """Retrieve your own avatar image so you can see or describe it. \
Call this when the user asks what you look like, wants to see your avatar, or mentions your appearance."""
        if not bot:
            return "Error: no bot context"
        from django.core.files.storage import default_storage

        from workspace.users.services.avatar import get_avatar_path, has_avatar

        if not has_avatar(bot):
            return "You do not have an avatar set."
        try:
            path = get_avatar_path(bot.id)
            with default_storage.open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return json.dumps(
                {
                    "type": "image",
                    "mime_type": "image/webp",
                    "data": b64,
                }
            )
        except Exception:
            logger.warning("Could not read avatar for bot %s", bot.id)
            return "Error: could not read avatar file."


class WebToolProvider(ToolProvider):
    """Web search and page reading. Registered only when SEARXNG_URL is set."""

    @tool(
        badge_icon="🔍",
        badge_label="Searched the web",
        detail_key="query",
        params=WebSearchParams,
    )
    def web_search(self, args, user, bot, conversation_id, context):
        """Search the web for current information. \
Call this when the user asks about recent events, news, facts you're unsure about, \
or anything that requires up-to-date information you don't have."""
        from .services.web import search

        query = args.query.strip()
        if not query:
            return "Error: query is required"

        results = search(query, max_results=5)
        if not results:
            return "No results found."

        return json.dumps(results, ensure_ascii=False)

    @tool(
        badge_icon="🌐",
        badge_label="Read webpage",
        detail_key="url",
        params=ReadWebpageParams,
    )
    def read_webpage(self, args, user, bot, conversation_id, context):
        """Fetch and extract the main text content of a webpage. \
Call this when you need to read the content of a specific URL shared by the user \
or found via web_search to get more details."""
        from .services.web import fetch_and_extract

        url = args.url.strip()
        if not url:
            return "Error: url is required"

        try:
            text = fetch_and_extract(url)
        except ValueError as exc:
            return f"Error: {exc}"

        if not text:
            return "Could not extract text content from this page."
        return text


class WeatherToolProvider(ToolProvider):
    """Current weather lookup by place name (Open-Meteo, keyless)."""

    @tool(
        badge_icon="🌤️",
        badge_label="Checked the weather",
        detail_key="location",
        params=GetWeatherParams,
    )
    def get_weather(self, args, user, bot, conversation_id, context):
        """Get the current weather for a place given its name. \
Call this when the user asks about the weather, temperature, or conditions somewhere \
(e.g. "what's the weather in Paris?", "is it raining in Tokyo?"). \
Returns temperature, feels-like, humidity, wind, and a description of the conditions."""
        from .services.weather import get_current_weather

        location = args.location.strip()
        if not location:
            return "Error: location is required"

        weather = get_current_weather(location)
        if weather is None:
            return f'Could not find weather for "{location}".'

        return json.dumps(weather, ensure_ascii=False)


class ImageToolProvider(ToolProvider):
    """Registered only when AI_IMAGE_MODEL is configured."""

    @tool(
        badge_icon="🎨",
        badge_label="Generated image",
        detail_key="prompt",
        params=GenerateImageParams,
    )
    def generate_image(self, args, user, bot, conversation_id, context):
        """Generate a brand-new image from a text description. \
Call this when the user asks you to create, draw, generate, make an image from scratch, send a picture or a photo of itself, or any other image-related request. \
Do NOT use this to modify an existing image — use edit_image instead."""
        prompt = args.prompt.strip()
        if not prompt:
            return "Error: prompt is required"
        if not conversation_id:
            return "Error: no conversation context"

        client = get_image_client()
        if not client:
            return "Error: AI is not configured"

        size = args.size
        if size not in ("1024x1024", "1792x1024", "1024x1792"):
            size = "1024x1024"
        logger.info(
            "Starting image generation: model=%s size=%s prompt=%.80s",
            settings.AI_IMAGE_MODEL,
            size,
            scrub(prompt),
        )
        from workspace.ai.metrics import AI_IMAGE_REQUESTS

        try:
            response = client.images.generate(
                model=settings.AI_IMAGE_MODEL,
                prompt=prompt,
                size=size,
                n=1,
                response_format="b64_json",
            )
        except Exception as e:
            AI_IMAGE_REQUESTS.labels(
                model=settings.AI_IMAGE_MODEL,
                op="generate",
                status="error",
            ).inc()
            logger.exception("Image generation failed")
            return f"Error: image generation failed — {e}"
        data = getattr(response, "data", None) or []
        b64 = data[0].b64_json if data else None
        try:
            image_data = base64.b64decode(b64) if b64 else b""
        except binascii.Error, ValueError:
            image_data = b""
        if not image_data:
            AI_IMAGE_REQUESTS.labels(
                model=settings.AI_IMAGE_MODEL,
                op="generate",
                status="error",
            ).inc()
            logger.error(
                "Image generation returned no image: model=%s size=%s prompt=%.80s",
                settings.AI_IMAGE_MODEL,
                size,
                scrub(prompt),
            )
            return "Error: the image model returned no image — generation failed"

        AI_IMAGE_REQUESTS.labels(
            model=settings.AI_IMAGE_MODEL,
            op="generate",
            status="ok",
        ).inc()

        logger.info(
            "Image generated: model=%s size=%s bytes=%d prompt=%.80s",
            settings.AI_IMAGE_MODEL,
            size,
            len(image_data),
            scrub(prompt),
        )

        context.setdefault("images", []).append(
            {
                "data": image_data,
                "prompt": prompt,
                "size": size,
            }
        )

        confirmation = f"Image generated successfully for: {prompt}"
        if _bot_supports_vision(bot):
            return _image_tool_payload(image_data, confirmation)
        return confirmation

    @tool(
        badge_icon="✏️",
        badge_label="Edited image",
        detail_key="prompt",
        params=EditImageParams,
    )
    def edit_image(self, args, user, bot, conversation_id, context):
        """Edit an existing image from the conversation based on a text instruction. \
Automatically uses the most recent image in the conversation as the source. \
Call this when the user asks you to modify, change, update, transform, or edit a picture — \
for example "make it darker", "remove the background", "add a hat". \
Do NOT use this to create an image from scratch — use generate_image instead."""
        from .services.image import ai_edit_image

        prompt = args.prompt.strip()
        if not prompt:
            return "Error: prompt is required"
        if not conversation_id:
            return "Error: no conversation context"

        # Prefer the image produced earlier in this same turn: it is not an
        # attachment yet (attachments are created after the loop completes),
        # so the DB lookup below would silently pick an older image.
        turn_images = context.get("images") or []
        if turn_images:
            source_data = turn_images[-1]["data"]
        else:
            from workspace.chat.models import MessageAttachment

            attachment = (
                MessageAttachment.objects.filter(
                    message__conversation_id=conversation_id,
                    mime_type__startswith="image/",
                )
                .order_by("-message__created_at", "-created_at")
                .first()
            )
            if not attachment:
                return "Error: no image found in the conversation to edit"

            try:
                source_data = attachment.file.read()
            except Exception:
                logger.warning(
                    "Could not read attachment %s for editing",
                    scrub(str(attachment.uuid)),
                )
                return "Error: could not read the source image"

        size = args.size

        try:
            image_data = ai_edit_image(source_data, prompt, size)
        except (ValueError, RuntimeError) as exc:
            return f"Error: {exc}"

        context.setdefault("images", []).append(
            {
                "data": image_data,
                "prompt": prompt,
                "size": size,
            }
        )

        confirmation = f"Image edited successfully: {prompt}"
        if _bot_supports_vision(bot):
            return _image_tool_payload(image_data, confirmation)
        return confirmation


def _parse_local_datetime(value: str, user_tz):
    """Parse an ISO datetime string, interpreting naive values in *user_tz*.

    Returns ``None`` when the string cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=user_tz)
    return dt


class AgentGoalToolProvider(ToolProvider):
    """Long-horizon autonomous goal tools for bots."""

    @tool(
        badge_icon="\U0001f3af",
        badge_label="Created goal",
        detail_key="title",
        params=CreateAgentGoalParams,
    )
    def create_agent_goal(self, args, user, bot, conversation_id, context):
        """Create a long-term autonomous goal that you will pursue across days, weeks or months. \
Call this when the user gives you a lasting mission: track something over time, coach them toward \
an objective, research a topic in depth, follow up until something is done. You will wake up at \
each check-in without user interaction, work on the goal with your tools, and decide yourself \
when to check in next and whether to message the user. \
IMPORTANT: Always call list_agent_goals first — update the existing goal instead of creating a duplicate."""
        from workspace.users.services.settings import get_user_timezone

        from .models import AgentGoal

        title = args.title.strip()[:200]
        goal = args.goal.strip()
        if not title or not goal:
            return "Error: title and goal are required"
        if not conversation_id:
            return "Error: no conversation context"

        active_count = AgentGoal.objects.filter(
            conversation_id=conversation_id,
            bot=bot,
            status=AgentGoal.Status.ACTIVE,
        ).count()
        if active_count >= AgentGoal.MAX_ACTIVE_PER_CONVERSATION:
            return (
                f"Error: this conversation already has {active_count} active goals "
                f"(max {AgentGoal.MAX_ACTIVE_PER_CONVERSATION}). Complete or abandon "
                f"one before creating a new goal."
            )

        user_tz = get_user_timezone(user)
        first_check = _parse_local_datetime(args.first_check_at.strip(), user_tz)
        if first_check is None:
            return (
                f'Error: could not parse datetime "{args.first_check_at}". '
                f"Use ISO format like 2026-03-10T09:00"
            )
        first_check = AgentGoal.clamp_next_check(first_check)

        deadline = None
        if args.deadline.strip():
            deadline = _parse_local_datetime(args.deadline.strip(), user_tz)
            if deadline is None:
                return (
                    f'Error: could not parse deadline "{args.deadline}". '
                    f"Use ISO format like 2026-06-01T18:00"
                )

        agent_goal = AgentGoal.objects.create(
            conversation_id=conversation_id,
            bot=bot,
            created_by=user,
            title=title,
            goal=goal,
            deadline=deadline,
            next_check_at=first_check,
        )
        logger.info(
            "Agent goal created: %s bot=%s conversation=%s",
            agent_goal.uuid,
            scrub(bot.username),
            scrub(conversation_id),
        )
        first_local = first_check.astimezone(user_tz)
        return (
            f'Created goal "{title}" (id: {agent_goal.uuid}). First check-in: '
            f"{first_local.strftime('%Y-%m-%d %H:%M')} ({user_tz})."
        )

    @tool(badge_icon="\U0001f3af", badge_label="Listed goals")
    def list_agent_goals(self, args, user, bot, conversation_id, context):
        """List your active and paused long-term goals in this conversation, including \
their private notes, next check-in and deadline. Call this before creating a goal (to avoid \
duplicates) and whenever the user asks what you are working on autonomously."""
        from workspace.users.services.settings import get_user_timezone

        from .models import AgentGoal

        goals = AgentGoal.objects.filter(
            conversation_id=conversation_id,
            bot=bot,
            status__in=[AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED],
        )
        if not goals.exists():
            return "No active goals in this conversation."

        user_tz = get_user_timezone(user)
        lines = []
        for g in goals:
            next_local = g.next_check_at.astimezone(user_tz)
            line = (
                f'- {g.uuid}: "{g.title}" [{g.status}] — '
                f"next check-in {next_local.strftime('%Y-%m-%d %H:%M')} ({user_tz}), "
                f"{g.check_count} check-in(s) so far"
            )
            if g.deadline:
                line += f", deadline {g.deadline.astimezone(user_tz).strftime('%Y-%m-%d %H:%M')}"
            line += f"\n  Objective: {g.goal[:200]}"
            if g.notes:
                line += f"\n  Notes: {g.notes[:300]}"
            lines.append(line)

        return f"Goals ({len(lines)}):\n" + "\n".join(lines)

    @tool(
        badge_icon="\U0001f4dd",
        badge_label="Updated goal",
        detail_key="notes",
        params=UpdateAgentGoalParams,
    )
    def update_agent_goal(self, args, user, bot, conversation_id, context):
        """Update one of your long-term goals: rewrite your private working notes, set your \
next check-in time, change the deadline, or pause/resume it. During an autonomous check-in, \
ALWAYS call this to save your updated notes and choose your next check-in time."""
        from workspace.users.services.settings import get_user_timezone

        from .models import AgentGoal

        goal = AgentGoal.objects.filter(
            uuid=args.goal_id,
            conversation_id=conversation_id,
            bot=bot,
            status__in=[AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED],
        ).first()
        if goal is None:
            return f"Error: no open goal found with id {args.goal_id}"

        user_tz = get_user_timezone(user)
        update_fields = ["updated_at"]
        changes = []

        if args.notes.strip():
            goal.notes = args.notes.strip()
            update_fields.append("notes")
            changes.append("notes saved")

        if args.next_check_at.strip():
            next_check = _parse_local_datetime(args.next_check_at.strip(), user_tz)
            if next_check is None:
                return (
                    f'Error: could not parse datetime "{args.next_check_at}". '
                    f"Use ISO format like 2026-03-10T09:00"
                )
            goal.next_check_at = AgentGoal.clamp_next_check(next_check)
            update_fields.append("next_check_at")
            next_local = goal.next_check_at.astimezone(user_tz)
            changes.append(
                f"next check-in {next_local.strftime('%Y-%m-%d %H:%M')} ({user_tz})"
            )

        if args.deadline.strip():
            deadline = _parse_local_datetime(args.deadline.strip(), user_tz)
            if deadline is None:
                return (
                    f'Error: could not parse deadline "{args.deadline}". '
                    f"Use ISO format like 2026-06-01T18:00"
                )
            goal.deadline = deadline
            update_fields.append("deadline")
            changes.append("deadline updated")

        if args.status.strip():
            status_value = args.status.strip().lower()
            if status_value not in (
                AgentGoal.Status.ACTIVE,
                AgentGoal.Status.PAUSED,
            ):
                return (
                    'Error: status can only be set to "active" or "paused". '
                    "Use complete_agent_goal to close a goal."
                )
            goal.status = status_value
            update_fields.append("status")
            changes.append(f"status → {status_value}")

        if not changes:
            return "Error: nothing to update — provide notes, next_check_at, deadline or status"

        goal.save(update_fields=update_fields)
        return f'Updated goal "{goal.title}": ' + ", ".join(changes) + "."

    @tool(
        badge_icon="\U0001f3c1",
        badge_label="Closed goal",
        detail_key="outcome",
        params=CompleteAgentGoalParams,
    )
    def complete_agent_goal(self, args, user, bot, conversation_id, context):
        """Close a long-term goal, either achieved or abandoned, with a final outcome summary. \
Call this when the goal is reached, has become irrelevant, or the user asks you to stop pursuing it."""
        from .models import AgentGoal

        goal = AgentGoal.objects.filter(
            uuid=args.goal_id,
            conversation_id=conversation_id,
            bot=bot,
            status__in=[AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED],
        ).first()
        if goal is None:
            return f"Error: no open goal found with id {args.goal_id}"

        outcome = args.outcome.strip()
        if not outcome:
            return "Error: outcome is required"

        goal.status = (
            AgentGoal.Status.ABANDONED if args.abandoned else AgentGoal.Status.COMPLETED
        )
        goal.outcome = outcome
        goal.save(update_fields=["status", "outcome", "updated_at"])
        logger.info(
            "Agent goal closed (%s): %s bot=%s",
            goal.status,
            goal.uuid,
            scrub(bot.username),
        )
        verb = "abandoned" if args.abandoned else "completed"
        return f'Goal "{goal.title}" marked as {verb}.'


class ScheduleToolProvider(ToolProvider):
    """Scheduled message tools for bots."""

    @tool(
        badge_icon="\u23f0",
        badge_label="Scheduled message",
        detail_key="prompt",
        params=ScheduleMessageParams,
    )
    def schedule_message(self, args, user, bot, conversation_id, context):
        """Schedule a message to be sent later, either once at a specific time or on a recurring basis. \
Call this when the user asks you to send a message later, set a reminder, or create a recurring message. \
IMPORTANT: Before creating a new schedule, always call list_schedules first to check for existing \
schedules with a similar prompt — update or cancel the old one instead of creating duplicates."""
        import calendar
        from datetime import datetime, time, timedelta

        from django.utils import timezone as dj_timezone

        from workspace.users.services.settings import get_user_timezone

        from .models import ScheduledMessage

        prompt = args.prompt.strip()
        if not prompt:
            return "Error: prompt is required"

        at = args.at.strip()
        every = args.every.strip()

        if at and every:
            return 'Error: provide either "at" for one-time or "every" for recurring, not both'
        if not at and not every:
            return 'Error: provide either "at" (ISO datetime) for one-time or "every" (hours/days/weeks/months) for recurring'

        user_tz = get_user_timezone(user)
        now = dj_timezone.now()

        if at:
            # One-time schedule
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f'Error: could not parse datetime "{at}". Use ISO format like 2026-03-10T09:00'
            # Interpret naive datetimes in the user's timezone
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=user_tz)
            if dt <= now:
                return "Error: scheduled time must be in the future"

            schedule = ScheduledMessage.objects.create(
                conversation_id=conversation_id,
                bot=bot,
                created_by=user,
                prompt=prompt,
                kind=ScheduledMessage.Kind.ONCE,
                scheduled_at=dt,
                next_run_at=dt,
            )
            dt_local = dt.astimezone(user_tz)
            return f"Scheduled one-time message for {dt_local.strftime('%Y-%m-%d %H:%M')} ({user_tz}) (id: {schedule.uuid})"

        # Recurring schedule
        valid_units = ["hours", "days", "weeks", "months"]
        if every not in valid_units:
            return f'Error: "every" must be one of {valid_units}'

        interval = args.interval
        if interval < 1:
            return "Error: interval must be a positive integer"

        at_time_str = args.at_time.strip()
        on_day = args.on_day

        recurrence_time = None
        if at_time_str:
            try:
                parts = at_time_str.split(":")
                recurrence_time = time(int(parts[0]), int(parts[1]))
            except ValueError, IndexError:
                return f'Error: could not parse time "{at_time_str}". Use HH:MM format (24h)'

        recurrence_day = None
        if on_day is not None:
            recurrence_day = on_day

        # Compute first next_run_at — recurrence_time is in the user's timezone
        now_local = now.astimezone(user_tz)
        if every == "hours":
            next_run = now + timedelta(hours=interval)
        elif every == "days":
            candidate = now_local + timedelta(days=interval)
            if recurrence_time is not None:
                candidate = candidate.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
            next_run = candidate.astimezone(UTC)
        elif every == "weeks":
            candidate = now_local + timedelta(weeks=interval)
            if recurrence_day is not None:
                current_weekday = candidate.weekday()
                day_offset = (recurrence_day - current_weekday) % 7
                candidate = candidate + timedelta(days=day_offset)
            if recurrence_time is not None:
                candidate = candidate.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
            next_run = candidate.astimezone(UTC)
        elif every == "months":
            year = now_local.year
            month = now_local.month + interval
            year += (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = now_local.day
            if recurrence_day is not None:
                day = recurrence_day
            max_day = calendar.monthrange(year, month)[1]
            day = min(day, max_day)
            candidate = now_local.replace(year=year, month=month, day=day)
            if recurrence_time is not None:
                candidate = candidate.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
            next_run = candidate.astimezone(UTC)

        schedule = ScheduledMessage.objects.create(
            conversation_id=conversation_id,
            bot=bot,
            created_by=user,
            prompt=prompt,
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=every,
            recurrence_interval=interval,
            recurrence_time=recurrence_time,
            recurrence_day=recurrence_day,
            next_run_at=next_run,
        )

        detail = f"every {interval} {every}"
        if recurrence_time:
            detail += f" at {recurrence_time.strftime('%H:%M')}"
        if recurrence_day is not None:
            if every == "weeks":
                day_names = [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]
                detail += f" on {day_names[recurrence_day]}"
            elif every == "months":
                detail += f" on day {recurrence_day}"

        next_local = next_run.astimezone(user_tz)
        return f"Scheduled recurring message ({detail}), next run: {next_local.strftime('%Y-%m-%d %H:%M')} ({user_tz}) (id: {schedule.uuid})"

    @tool(
        badge_icon="\u274c",
        badge_label="Cancelled schedule",
        detail_key="schedule_id",
        params=CancelScheduleParams,
    )
    def cancel_schedule(self, args, user, bot, conversation_id, context):
        """Cancel an active scheduled message by its ID. \
Call this when the user wants to stop or remove a previously scheduled message."""
        from .models import ScheduledMessage

        schedule_id = args.schedule_id.strip()
        if not schedule_id:
            return "Error: schedule_id is required"

        try:
            schedule = ScheduledMessage.objects.get(
                uuid=schedule_id,
                conversation_id=conversation_id,
                bot=bot,
                is_active=True,
            )
        except ScheduledMessage.DoesNotExist:
            return f"Error: no active schedule found with id {schedule_id}"

        schedule.is_active = False
        schedule.save(update_fields=["is_active", "updated_at"])
        return f"Cancelled schedule {schedule_id}."

    @tool(badge_icon="\U0001f4cb", badge_label="Listed schedules")
    def list_schedules(self, args, user, bot, conversation_id, context):
        """List all active scheduled messages in this conversation. \
Call this when the user wants to see what messages are scheduled or pending."""
        from workspace.users.services.settings import get_user_timezone

        from .models import ScheduledMessage

        schedules = ScheduledMessage.objects.filter(
            conversation_id=conversation_id,
            bot=bot,
            is_active=True,
        )

        if not schedules.exists():
            return "No active schedules in this conversation."

        user_tz = get_user_timezone(user)
        lines = []
        for s in schedules:
            next_local = s.next_run_at.astimezone(user_tz)
            if s.kind == ScheduledMessage.Kind.ONCE:
                timing = f"once at {next_local.strftime('%Y-%m-%d %H:%M')} ({user_tz})"
            else:
                timing = f"every {s.recurrence_interval} {s.recurrence_unit}"
                if s.recurrence_time:
                    timing += f" at {s.recurrence_time.strftime('%H:%M')}"
                if s.recurrence_day is not None:
                    if s.recurrence_unit == "weeks":
                        day_names = [
                            "Monday",
                            "Tuesday",
                            "Wednesday",
                            "Thursday",
                            "Friday",
                            "Saturday",
                            "Sunday",
                        ]
                        timing += f" on {day_names[s.recurrence_day]}"
                    elif s.recurrence_unit == "months":
                        timing += f" on day {s.recurrence_day}"
                timing += (
                    f", next run: {next_local.strftime('%Y-%m-%d %H:%M')} ({user_tz})"
                )
            lines.append(f'- {s.uuid}: "{s.prompt[:60]}" — {timing}')

        return f"Active schedules ({len(lines)}):\n" + "\n".join(lines)
