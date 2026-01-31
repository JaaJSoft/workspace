from dataclasses import asdict

from workspace.core.module_registry import registry


def workspace_modules(request):
    return {
        'workspace_modules': registry.get_for_template(),
        'workspace_active_modules': [asdict(m) for m in registry.get_active()],
    }
