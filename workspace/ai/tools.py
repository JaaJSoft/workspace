"""Core AI chat tools (memory, workspace search, avatar, image generation)."""
import base64
import json
import logging

from django.conf import settings

from .client import get_image_client
from .models import UserMemory
from pydantic import BaseModel, Field

from .tool_registry import ToolProvider, tool

logger = logging.getLogger(__name__)


class SaveMemoryParams(BaseModel):
    key: str = Field(description="A short category label (e.g. name, language, project, preference).")
    content: str = Field(description="The fact to remember.")


class DeleteMemoryParams(BaseModel):
    key: str = Field(description="The key of the memory to delete.")


class WebSearchParams(BaseModel):
    query: str = Field(description="The search query.")


class ReadWebpageParams(BaseModel):
    url: str = Field(description="The URL of the webpage to read.")


class GenerateImageParams(BaseModel):
    prompt: str = Field(description="A detailed description of the image to generate.")
    size: str = Field(default="1024x1024", description="Image size: 1024x1024, 1792x1024, or 1024x1792.")


class EditImageParams(BaseModel):
    prompt: str = Field(description="A description of the changes to apply to the image.")
    size: str = Field(default="1024x1024", description="Output size: 1024x1024, 1792x1024, or 1024x1792.")


class ScheduleMessageParams(BaseModel):
    prompt: str = Field(description="The instruction/intent for the future message.")
    at: str = Field(default="", description="ISO datetime for one-time scheduling (e.g. 2026-03-10T09:00). Mutually exclusive with every/interval.")
    every: str = Field(default="", description="Recurrence unit: hours, days, weeks, months. Mutually exclusive with at.")
    interval: int = Field(default=1, description="Recurrence interval (default 1).")
    at_time: str = Field(default="", description="Time of day for daily/weekly/monthly recurrence (HH:MM, 24h format).")
    on_day: int | None = Field(default=None, description="Day of week (0=Mon..6=Sun) for weekly, or day of month (1-31) for monthly.")


class CancelScheduleParams(BaseModel):
    schedule_id: str = Field(description="UUID of the schedule to cancel.")


class CoreToolProvider(ToolProvider):

    @tool(badge_icon='👤', badge_label='Looked up profile')
    def get_current_user_info(self, args, user, bot, conversation_id, context):
        """Get the profile of the user you are chatting with: username, full name, email, and join date. \
Call this when you need to address the user by name, check their email, or answer questions about their account."""
        if not user:
            return 'Error: no user context'
        info = {
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'date_joined': user.date_joined.strftime('%Y-%m-%d'),
        }
        return json.dumps(info)

    @tool(badge_icon='🧠', badge_label='Retained', detail_key='key', params=SaveMemoryParams)
    def save_memory(self, args, user, bot, conversation_id, context):
        """Persistently save a fact about the user so you can recall it in future conversations. \
Call this proactively when the user tells you their name, preferences, projects, or any personal detail worth remembering. \
If the key already exists it will be updated."""
        key = args.key.strip()[:100]
        content = args.content.strip()
        if not key or not content:
            return 'Error: key and content are required'
        UserMemory.objects.update_or_create(
            user=user, bot=bot, key=key,
            defaults={'content': content},
        )
        logger.info('Memory saved: %s/%s — %s', user.username, bot.username, key)
        return f'Saved memory "{key}".'

    @tool(badge_icon='🧠', badge_label='Forgot', detail_key='key', params=DeleteMemoryParams)
    def delete_memory(self, args, user, bot, conversation_id, context):
        """Delete a previously saved memory. \
Call this when the user explicitly asks you to forget something or when a stored fact is no longer correct."""
        key = args.key.strip()
        deleted, _ = UserMemory.objects.filter(user=user, bot=bot, key=key).delete()
        if deleted:
            logger.info('Memory deleted: %s/%s — %s', user.username, bot.username, key)
            return f'Deleted memory "{key}".'
        return f'Memory "{key}" not found.'

    @tool(badge_icon='🖼️', badge_label='Viewed own avatar')
    def get_my_avatar(self, args, user, bot, conversation_id, context):
        """Retrieve your own avatar image so you can see or describe it. \
Call this when the user asks what you look like, wants to see your avatar, or mentions your appearance."""
        if not bot:
            return 'Error: no bot context'
        from django.core.files.storage import default_storage
        from workspace.users.services.avatar import get_avatar_path, has_avatar
        if not has_avatar(bot):
            return 'You do not have an avatar set.'
        try:
            path = get_avatar_path(bot.id)
            with default_storage.open(path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            return json.dumps({
                'type': 'image',
                'mime_type': 'image/webp',
                'data': b64,
            })
        except Exception:
            logger.warning('Could not read avatar for bot %s', bot.id)
            return 'Error: could not read avatar file.'



class WebToolProvider(ToolProvider):
    """Web search and page reading. Registered only when SEARXNG_URL is set."""

    @tool(badge_icon='🔍', badge_label='Searched the web', detail_key='query', params=WebSearchParams)
    def web_search(self, args, user, bot, conversation_id, context):
        """Search the web for current information. \
Call this when the user asks about recent events, news, facts you're unsure about, \
or anything that requires up-to-date information you don't have."""
        from .services.web import search

        query = args.query.strip()
        if not query:
            return 'Error: query is required'

        results = search(query, max_results=5)
        if not results:
            return 'No results found.'

        return json.dumps(results, ensure_ascii=False)

    @tool(badge_icon='🌐', badge_label='Read webpage', detail_key='url', params=ReadWebpageParams)
    def read_webpage(self, args, user, bot, conversation_id, context):
        """Fetch and extract the main text content of a webpage. \
Call this when you need to read the content of a specific URL shared by the user \
or found via web_search to get more details."""
        from .services.web import fetch_and_extract

        url = args.url.strip()
        if not url:
            return 'Error: url is required'

        try:
            text = fetch_and_extract(url)
        except ValueError as exc:
            return f'Error: {exc}'

        if not text:
            return 'Could not extract text content from this page.'
        return text


class ImageToolProvider(ToolProvider):
    """Registered only when AI_IMAGE_MODEL is configured."""

    @tool(badge_icon='🎨', badge_label='Generated image', detail_key='prompt', params=GenerateImageParams)
    def generate_image(self, args, user, bot, conversation_id, context):
        """Generate a brand-new image from a text description. \
Call this when the user asks you to create, draw, generate, make an image from scratch, send a picture or a photo of itself, or any other image-related request. \
Do NOT use this to modify an existing image — use edit_image instead."""
        prompt = args.prompt.strip()
        if not prompt:
            return 'Error: prompt is required'
        if not conversation_id:
            return 'Error: no conversation context'

        client = get_image_client()
        if not client:
            return 'Error: AI is not configured'

        size = args.size
        if size not in ('1024x1024', '1792x1024', '1024x1792'):
            size = '1024x1024'
        logger.info(
            'Starting image generation: model=%s size=%s prompt=%.80s',
            settings.AI_IMAGE_MODEL, size, prompt,
        )
        try:
            response = client.images.generate(
                model=settings.AI_IMAGE_MODEL,
                prompt=prompt,
                size=size,
                n=1,
                response_format='b64_json',
            )
        except Exception as e:
            logger.exception('Image generation failed')
            return f'Error: image generation failed — {e}'

        image_data = base64.b64decode(response.data[0].b64_json)
        logger.info(
            'Image generated: model=%s size=%s bytes=%d prompt=%.80s',
            settings.AI_IMAGE_MODEL, size, len(image_data), prompt,
        )

        context.setdefault('images', []).append({
            'data': image_data,
            'prompt': prompt,
            'size': size,
        })

        return f'Image generated successfully for: {prompt}'

    @tool(badge_icon='✏️', badge_label='Edited image', detail_key='prompt', params=EditImageParams)
    def edit_image(self, args, user, bot, conversation_id, context):
        """Edit an existing image from the conversation based on a text instruction. \
Automatically uses the most recent image in the conversation as the source. \
Call this when the user asks you to modify, change, update, transform, or edit a picture — \
for example "make it darker", "remove the background", "add a hat". \
Do NOT use this to create an image from scratch — use generate_image instead."""
        from .services.image import ai_edit_image

        prompt = args.prompt.strip()
        if not prompt:
            return 'Error: prompt is required'
        if not conversation_id:
            return 'Error: no conversation context'

        # Find the most recent image attachment in the conversation
        from workspace.chat.models import MessageAttachment
        attachment = (
            MessageAttachment.objects.filter(
                message__conversation_id=conversation_id,
                mime_type__startswith='image/',
            )
            .order_by('-message__created_at', '-created_at')
            .first()
        )
        if not attachment:
            return 'Error: no image found in the conversation to edit'

        try:
            source_data = attachment.file.read()
        except Exception:
            logger.warning('Could not read attachment %s for editing', attachment.uuid)
            return 'Error: could not read the source image'

        size = args.size

        try:
            image_data = ai_edit_image(source_data, prompt, size)
        except (ValueError, RuntimeError) as exc:
            return f'Error: {exc}'

        context.setdefault('images', []).append({
            'data': image_data,
            'prompt': prompt,
            'size': size,
        })

        return f'Image edited successfully: {prompt}'


class ScheduleToolProvider(ToolProvider):
    """Scheduled message tools for bots."""

    @tool(badge_icon='\u23f0', badge_label='Scheduled message', detail_key='prompt', params=ScheduleMessageParams)
    def schedule_message(self, args, user, bot, conversation_id, context):
        """Schedule a message to be sent later, either once at a specific time or on a recurring basis. \
Call this when the user asks you to send a message later, set a reminder, or create a recurring message. \
IMPORTANT: Before creating a new schedule, always call list_schedules first to check for existing \
schedules with a similar prompt — update or cancel the old one instead of creating duplicates."""
        from datetime import datetime, time, timedelta, timezone
        import calendar
        from django.utils import timezone as dj_timezone
        from workspace.users.services.settings import get_user_timezone
        from .models import ScheduledMessage

        prompt = args.prompt.strip()
        if not prompt:
            return 'Error: prompt is required'

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
                return 'Error: scheduled time must be in the future'

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
            return f'Scheduled one-time message for {dt_local.strftime("%Y-%m-%d %H:%M")} ({user_tz}) (id: {schedule.uuid})'

        # Recurring schedule
        valid_units = ['hours', 'days', 'weeks', 'months']
        if every not in valid_units:
            return f'Error: "every" must be one of {valid_units}'

        interval = args.interval
        if interval < 1:
            return 'Error: interval must be a positive integer'

        at_time_str = args.at_time.strip()
        on_day = args.on_day

        recurrence_time = None
        if at_time_str:
            try:
                parts = at_time_str.split(':')
                recurrence_time = time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                return f'Error: could not parse time "{at_time_str}". Use HH:MM format (24h)'

        recurrence_day = None
        if on_day is not None:
            recurrence_day = on_day

        # Compute first next_run_at — recurrence_time is in the user's timezone
        now_local = now.astimezone(user_tz)
        if every == 'hours':
            next_run = now + timedelta(hours=interval)
        elif every == 'days':
            candidate = now_local + timedelta(days=interval)
            if recurrence_time is not None:
                candidate = candidate.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
            next_run = candidate.astimezone(timezone.utc)
        elif every == 'weeks':
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
            next_run = candidate.astimezone(timezone.utc)
        elif every == 'months':
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
            next_run = candidate.astimezone(timezone.utc)

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

        detail = f'every {interval} {every}'
        if recurrence_time:
            detail += f' at {recurrence_time.strftime("%H:%M")}'
        if recurrence_day is not None:
            if every == 'weeks':
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                detail += f' on {day_names[recurrence_day]}'
            elif every == 'months':
                detail += f' on day {recurrence_day}'

        next_local = next_run.astimezone(user_tz)
        return f'Scheduled recurring message ({detail}), next run: {next_local.strftime("%Y-%m-%d %H:%M")} ({user_tz}) (id: {schedule.uuid})'

    @tool(badge_icon='\u274c', badge_label='Cancelled schedule', detail_key='schedule_id', params=CancelScheduleParams)
    def cancel_schedule(self, args, user, bot, conversation_id, context):
        """Cancel an active scheduled message by its ID. \
Call this when the user wants to stop or remove a previously scheduled message."""
        from .models import ScheduledMessage

        schedule_id = args.schedule_id.strip()
        if not schedule_id:
            return 'Error: schedule_id is required'

        try:
            schedule = ScheduledMessage.objects.get(
                uuid=schedule_id,
                conversation_id=conversation_id,
                bot=bot,
                is_active=True,
            )
        except ScheduledMessage.DoesNotExist:
            return f'Error: no active schedule found with id {schedule_id}'

        schedule.is_active = False
        schedule.save(update_fields=['is_active', 'updated_at'])
        return f'Cancelled schedule {schedule_id}.'

    @tool(badge_icon='\U0001f4cb', badge_label='Listed schedules')
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
            return 'No active schedules in this conversation.'

        user_tz = get_user_timezone(user)
        lines = []
        for s in schedules:
            next_local = s.next_run_at.astimezone(user_tz)
            if s.kind == ScheduledMessage.Kind.ONCE:
                timing = f'once at {next_local.strftime("%Y-%m-%d %H:%M")} ({user_tz})'
            else:
                timing = f'every {s.recurrence_interval} {s.recurrence_unit}'
                if s.recurrence_time:
                    timing += f' at {s.recurrence_time.strftime("%H:%M")}'
                if s.recurrence_day is not None:
                    if s.recurrence_unit == 'weeks':
                        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                        timing += f' on {day_names[s.recurrence_day]}'
                    elif s.recurrence_unit == 'months':
                        timing += f' on day {s.recurrence_day}'
                timing += f', next run: {next_local.strftime("%Y-%m-%d %H:%M")} ({user_tz})'
            lines.append(f'- {s.uuid}: "{s.prompt[:60]}" — {timing}')

        return f'Active schedules ({len(lines)}):\n' + '\n'.join(lines)
