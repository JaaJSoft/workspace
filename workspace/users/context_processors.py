from workspace.users.services.settings import get_setting


def user_preferences(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    return {
        'user_theme': get_setting(user, 'core', 'theme') or 'light',
        'user_timezone': get_setting(user, 'core', 'timezone') or '',
    }
