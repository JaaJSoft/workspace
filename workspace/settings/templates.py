"""Template engine configuration."""

from .base import BASE_DIR

# No explicit "loaders": Django then wraps the filesystem and app_directories
# loaders in the cached loader in every mode, and runserver's autoreloader
# clears that cache when a template changes. Under `runserver --noreload`
# nothing clears it, so template edits need a server restart.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "workspace.core.context_processors.workspace_modules",
                "workspace.ai.context_processors.ai_context",
                "workspace.users.context_processors.user_preferences",
                # Expose `request_processing_ms` au template
                # 'workspace.ui.context_processors.request_timing',
            ],
        },
    },
]
