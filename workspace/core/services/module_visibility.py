"""Per-user module visibility.

Non-preview modules are visible to everyone. Preview modules follow the global
``settings.PREVIEW_VISIBILITY`` audience (all/staff/admin/none). This is UI
hiding only - it is not request-level enforcement.
"""

from django.conf import settings

from ..module_registry import ModuleVisibility, registry


def user_can_see_module(user, module) -> bool:
    """True if *user* may see *module* (a ModuleInfo) on the home page / nav."""
    if not module.preview:
        return True

    level = ModuleVisibility.normalize(settings.PREVIEW_VISIBILITY)
    if level == ModuleVisibility.ALL:
        return True
    if level == ModuleVisibility.NONE:
        return False
    if level == ModuleVisibility.STAFF:
        return bool(user.is_staff or user.is_superuser)
    if level == ModuleVisibility.ADMIN:
        return bool(user.is_superuser)
    # Unreachable today (normalize guarantees a known level); fail closed so a
    # future level added without updating this ladder hides previews, not leaks them.
    return False


def is_module_slug_visible(user, slug) -> bool:
    """Visibility check by slug. Unknown slugs are treated as visible."""
    module = registry.get(slug)
    return module is None or user_can_see_module(user, module)


def visible_modules(user):
    """Active modules *user* may see, in registry order."""
    return [m for m in registry.get_active() if user_can_see_module(user, m)]


def filter_visible_commands(user, commands):
    """Keep only commands whose owning module is visible to *user*."""
    return [cmd for cmd in commands if is_module_slug_visible(user, cmd.module_slug)]
