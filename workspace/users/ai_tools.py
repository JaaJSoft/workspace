"""AI chat tools for user presence."""
import json

from workspace.ai.tool_registry import Param, ToolProvider, tool


class UsersToolProvider(ToolProvider):

    @tool(badge_icon='👤', badge_label='Checked status', detail_key='username', params={
        'username': Param('The username of the person to check.'),
    })
    def check_user_status(self, args, user, bot, conversation_id, context):
        """Check the presence status of a colleague (online, away, busy, or offline). \
Use this when the user asks if someone is available or what their status is."""
        username = args.get('username', '').strip()
        if not username:
            return 'Error: username is required'
        from django.contrib.auth import get_user_model
        from workspace.users.presence_service import get_last_seen, get_status
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

    @tool(badge_icon='👥', badge_label='Listed online users', params={
        'limit': Param('Maximum number of users to return (default 20).', 'integer', required=False),
    })
    def list_online_users(self, args, user, bot, conversation_id, context):
        """List users who are currently online, away, or busy. \
Use this when the user asks who is available or who is online right now."""
        limit = min(int(args.get('limit', 20)), 50)
        from django.contrib.auth import get_user_model
        from workspace.users.presence_service import get_online_user_ids, get_statuses
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
