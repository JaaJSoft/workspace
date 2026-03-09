"""Core AI chat tools (memory, workspace search, avatar, image generation)."""
import base64
import io
import json
import logging

from django.conf import settings

from .client import get_image_client
from .models import UserMemory
from .tool_registry import Param, ToolProvider, tool

logger = logging.getLogger(__name__)


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

    @tool(badge_icon='🧠', badge_label='Retained', detail_key='key', params={
        'key': Param('A short category label (e.g. name, language, project, preference).'),
        'content': Param('The fact to remember.'),
    })
    def save_memory(self, args, user, bot, conversation_id, context):
        """Persistently save a fact about the user so you can recall it in future conversations. \
Call this proactively when the user tells you their name, preferences, projects, or any personal detail worth remembering. \
If the key already exists it will be updated."""
        key = args.get('key', '').strip()[:100]
        content = args.get('content', '').strip()
        if not key or not content:
            return 'Error: key and content are required'
        UserMemory.objects.update_or_create(
            user=user, bot=bot, key=key,
            defaults={'content': content},
        )
        logger.info('Memory saved: %s/%s — %s', user.username, bot.username, key)
        return f'Saved memory "{key}".'

    @tool(badge_icon='🧠', badge_label='Forgot', detail_key='key', params={
        'key': Param('The key of the memory to delete.'),
    })
    def delete_memory(self, args, user, bot, conversation_id, context):
        """Delete a previously saved memory. \
Call this when the user explicitly asks you to forget something or when a stored fact is no longer correct."""
        key = args.get('key', '').strip()
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
        from workspace.users.avatar_service import get_avatar_path, has_avatar
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



class ImageToolProvider(ToolProvider):
    """Registered only when AI_IMAGE_MODEL is configured."""

    @tool(badge_icon='🎨', badge_label='Generated image', detail_key='prompt', params={
        'prompt': Param('A detailed description of the image to generate.'),
        'size': Param('Image size: 1024x1024, 1792x1024, or 1024x1792.', required=False),
    })
    def generate_image(self, args, user, bot, conversation_id, context):
        """Generate a brand-new image from a text description. \
Call this when the user asks you to create, draw, generate, or make an image from scratch. \
Do NOT use this to modify an existing image — use edit_image instead."""
        prompt = args.get('prompt', '').strip()
        if not prompt:
            return 'Error: prompt is required'
        if not conversation_id:
            return 'Error: no conversation context'

        client = get_image_client()
        if not client:
            return 'Error: AI is not configured'

        size = args.get('size', '1024x1024')
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

    def _edit_via_openai(self, client, image_file, prompt, size):
        """Try editing via the OpenAI-compatible /v1/images/edits endpoint."""
        response = client.images.edit(
            model=settings.AI_IMAGE_MODEL,
            image=image_file,
            prompt=prompt,
            size=size,
            n=1,
            response_format='b64_json',
        )
        return base64.b64decode(response.data[0].b64_json)

    def _edit_via_ollama(self, source_data, prompt):
        """Fallback: use Ollama native /api/generate with images param (img2img)."""
        import httpx
        base_url = (settings.AI_IMAGE_BASE_URL or settings.AI_BASE_URL or '').rstrip('/')
        if base_url.endswith('/v1'):
            base_url = base_url[:-3]
        resp = httpx.post(
            f'{base_url}/api/generate',
            json={
                'model': settings.AI_IMAGE_MODEL,
                'prompt': prompt,
                'images': [base64.b64encode(source_data).decode()],
                'stream': False,
            },
            timeout=settings.AI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns 'image' (singular) for img2img
        result_b64 = data.get('image') or ''
        if not result_b64:
            raise RuntimeError(f'no image returned from Ollama — response keys: {list(data.keys())}')
        return base64.b64decode(result_b64)

    @tool(badge_icon='✏️', badge_label='Edited image', detail_key='prompt', params={
        'prompt': Param('A description of the changes to apply to the image.'),
        'size': Param('Output size: 1024x1024, 1792x1024, or 1024x1792.', required=False),
    })
    def edit_image(self, args, user, bot, conversation_id, context):
        """Edit an existing image from the conversation based on a text instruction. \
Automatically uses the most recent image in the conversation as the source. \
Call this when the user asks you to modify, change, update, transform, or edit a picture — \
for example "make it darker", "remove the background", "add a hat". \
Do NOT use this to create an image from scratch — use generate_image instead."""
        prompt = args.get('prompt', '').strip()
        if not prompt:
            return 'Error: prompt is required'
        if not conversation_id:
            return 'Error: no conversation context'

        client = get_image_client()
        if not client:
            return 'Error: AI is not configured'

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

        size = args.get('size', '1024x1024')
        if size not in ('1024x1024', '1792x1024', '1024x1792'):
            size = '1024x1024'

        logger.info(
            'Starting image edit: model=%s size=%s source=%s prompt=%.80s',
            settings.AI_IMAGE_MODEL, size, attachment.uuid, prompt,
        )

        # Try OpenAI-compatible endpoint first, fall back to Ollama native API
        try:
            image_file = io.BytesIO(source_data)
            image_file.name = attachment.original_name or 'image.png'
            image_data = self._edit_via_openai(client, image_file, prompt, size)
            logger.info('Image edited via OpenAI endpoint: model=%s bytes=%d', settings.AI_IMAGE_MODEL, len(image_data))
        except Exception as openai_err:
            logger.info('OpenAI images.edit failed (%s), falling back to Ollama native API', openai_err)
            try:
                image_data = self._edit_via_ollama(source_data, prompt)
                logger.info('Image edited via Ollama native API: model=%s bytes=%d', settings.AI_IMAGE_MODEL, len(image_data))
            except Exception as ollama_err:
                logger.exception('Image edit failed on both OpenAI and Ollama backends')
                return f'Error: image edit failed — {ollama_err}'

        context.setdefault('images', []).append({
            'data': image_data,
            'prompt': prompt,
            'size': size,
        })

        return f'Image edited successfully: {prompt}'


class ScheduleToolProvider(ToolProvider):
    """Scheduled message tools for bots."""

    @tool(badge_icon='\u23f0', badge_label='Scheduled message', detail_key='prompt', params={
        'prompt': Param('The instruction/intent for the future message.'),
        'at': Param('ISO datetime for one-time scheduling (e.g. 2026-03-10T09:00). Mutually exclusive with every/interval.', required=False),
        'every': Param('Recurrence unit: hours, days, weeks, months. Mutually exclusive with at.', required=False),
        'interval': Param('Recurrence interval (default 1).', type='integer', required=False),
        'at_time': Param('Time of day for daily/weekly/monthly recurrence (HH:MM, 24h format).', required=False),
        'on_day': Param('Day of week (0=Mon..6=Sun) for weekly, or day of month (1-31) for monthly.', type='integer', required=False),
    })
    def schedule_message(self, args, user, bot, conversation_id, context):
        """Schedule a message to be sent later, either once at a specific time or on a recurring basis. \
Call this when the user asks you to send a message later, set a reminder, or create a recurring message."""
        from datetime import datetime, time, timedelta
        import calendar
        from django.utils import timezone
        from .models import ScheduledMessage

        prompt = args.get('prompt', '').strip()
        if not prompt:
            return 'Error: prompt is required'

        at = args.get('at', '').strip() if args.get('at') else ''
        every = args.get('every', '').strip() if args.get('every') else ''

        if at and every:
            return 'Error: provide either "at" for one-time or "every" for recurring, not both'
        if not at and not every:
            return 'Error: provide either "at" (ISO datetime) for one-time or "every" (hours/days/weeks/months) for recurring'

        now = timezone.now()

        if at:
            # One-time schedule
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f'Error: could not parse datetime "{at}". Use ISO format like 2026-03-10T09:00'
            # Make timezone-aware if naive
            if dt.tzinfo is None:
                dt = timezone.make_aware(dt)
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
            return f'Scheduled one-time message for {dt.strftime("%Y-%m-%d %H:%M %Z")} (id: {schedule.uuid})'

        # Recurring schedule
        valid_units = ['hours', 'days', 'weeks', 'months']
        if every not in valid_units:
            return f'Error: "every" must be one of {valid_units}'

        interval = args.get('interval', 1)
        if not isinstance(interval, int) or interval < 1:
            return 'Error: interval must be a positive integer'

        at_time_str = args.get('at_time', '').strip() if args.get('at_time') else ''
        on_day = args.get('on_day')

        recurrence_time = None
        if at_time_str:
            try:
                parts = at_time_str.split(':')
                recurrence_time = time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                return f'Error: could not parse time "{at_time_str}". Use HH:MM format (24h)'

        recurrence_day = None
        if on_day is not None:
            if not isinstance(on_day, int):
                return 'Error: on_day must be an integer'
            recurrence_day = on_day

        # Compute first next_run_at
        if every == 'hours':
            next_run = now + timedelta(hours=interval)
        elif every == 'days':
            next_run = now + timedelta(days=interval)
            if recurrence_time is not None:
                next_run = next_run.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
        elif every == 'weeks':
            next_run = now + timedelta(weeks=interval)
            if recurrence_day is not None:
                current_weekday = next_run.weekday()
                day_offset = (recurrence_day - current_weekday) % 7
                next_run = next_run + timedelta(days=day_offset)
            if recurrence_time is not None:
                next_run = next_run.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )
        elif every == 'months':
            year = now.year
            month = now.month + interval
            year += (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = now.day
            if recurrence_day is not None:
                day = recurrence_day
            max_day = calendar.monthrange(year, month)[1]
            day = min(day, max_day)
            next_run = now.replace(year=year, month=month, day=day)
            if recurrence_time is not None:
                next_run = next_run.replace(
                    hour=recurrence_time.hour,
                    minute=recurrence_time.minute,
                    second=0,
                    microsecond=0,
                )

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

        return f'Scheduled recurring message ({detail}), next run: {next_run.strftime("%Y-%m-%d %H:%M %Z")} (id: {schedule.uuid})'

    @tool(badge_icon='\u274c', badge_label='Cancelled schedule', detail_key='schedule_id', params={
        'schedule_id': Param('UUID of the schedule to cancel.'),
    })
    def cancel_schedule(self, args, user, bot, conversation_id, context):
        """Cancel an active scheduled message by its ID. \
Call this when the user wants to stop or remove a previously scheduled message."""
        from .models import ScheduledMessage

        schedule_id = args.get('schedule_id', '').strip()
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
        from .models import ScheduledMessage

        schedules = ScheduledMessage.objects.filter(
            conversation_id=conversation_id,
            bot=bot,
            is_active=True,
        )

        if not schedules.exists():
            return 'No active schedules in this conversation.'

        lines = []
        for s in schedules:
            if s.kind == ScheduledMessage.Kind.ONCE:
                timing = f'once at {s.next_run_at.strftime("%Y-%m-%d %H:%M %Z")}'
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
                timing += f', next run: {s.next_run_at.strftime("%Y-%m-%d %H:%M %Z")}'
            lines.append(f'- {s.uuid}: "{s.prompt[:60]}" — {timing}')

        return f'Active schedules ({len(lines)}):\n' + '\n'.join(lines)
