from workspace.users.services.settings import get_module_settings


def user_preferences(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    # All four keys live in the core module, so fetch them in a single query
    # instead of one round-trip per get_setting call.
    core = get_module_settings(user, "core")
    return {
        "user_theme": core.get("theme") or "light",
        "user_light_theme": core.get("light_theme") or "light",
        "user_dark_theme": core.get("dark_theme") or "dark",
        "user_timezone": core.get("timezone") or "",
    }
