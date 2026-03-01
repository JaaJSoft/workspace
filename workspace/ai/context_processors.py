from workspace.ai.client import is_ai_enabled


def ai_context(request):
    return {'ai_enabled': is_ai_enabled()}
