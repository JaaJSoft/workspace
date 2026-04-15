"""AI chat tools for user presence."""
import json

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool


class CheckUserStatusParams(BaseModel):
    username: str = Field(description="The username of the person to check.")


class ListOnlineUsersParams(BaseModel):
    limit: int = Field(default=20, description="Maximum number of users to return (default 20).")


class UsersToolProvider(ToolProvider):

    @tool(badge_icon='👤', badge_label='Checked status', detail_key='username', params=CheckUserStatusParams)
    def check_user_status(self, args, user, bot, conversation_id, context):
        """Check whether a specific colleague is online, away, busy, or offline. \
Also returns their last-seen time if offline. \
Call this when the user asks if someone is available, reachable, or what their status is."""
        username = args.username.strip()
        if not username:
            return 'Error: username is required'
        from django.contrib.auth import get_user_model
        from workspace.users.services.presence import get_last_seen, get_status
        User = get_user_model()
        try:
            target = User.objects.get(username__iexact=username, is_active=True)
        except User.DoesNotExist:
            return f'User "{username}" not found.'
        status = get_status(target.id)
        display_name = target.get_full_name() or target.username
        last_seen = get_last_seen(target.id)
        info = {'username': target.username, 'display_name': display_name, 'status': status}
        if status == 'offline' and last_seen:
            info['last_seen'] = last_seen.strftime('%Y-%m-%d %H:%M')
        return json.dumps(info)

    @tool(badge_icon='👥', badge_label='Listed online users', params=ListOnlineUsersParams)
    def list_online_users(self, args, user, bot, conversation_id, context):
        """List all users who are currently online, away, or busy (excludes offline users and bots). \
Call this when the user asks who is available, who is online, or wants an overview of active colleagues."""
        limit = min(args.limit, 50)
        from django.contrib.auth import get_user_model
        from workspace.users.services.presence import get_online_user_ids, get_statuses
        User = get_user_model()
        online_ids = get_online_user_ids()
        if not online_ids:
            return 'No users are currently online.'
        statuses = get_statuses(online_ids)
        users = User.objects.filter(id__in=online_ids, is_active=True).exclude(
            bot_profile__isnull=False,
        )[:limit]
        results = []
        for u in users:
            display_name = u.get_full_name() or u.username
            status = statuses.get(u.id, 'offline')
            results.append(f'{display_name} (@{u.username}) — {status}')
        if not results:
            return 'No users are currently online.'
        return '\n'.join(results)
