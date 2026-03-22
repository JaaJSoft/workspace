from dataclasses import asdict

from django.conf import settings

from workspace.core.module_registry import registry


def workspace_modules(request):
    return {
        'workspace_active_modules': [asdict(m) for m in registry.get_active()],
        'workspace_commands': [asdict(c) for c in registry.get_active_commands()],
        'APP_VERSION': settings.APP_VERSION,
    }
