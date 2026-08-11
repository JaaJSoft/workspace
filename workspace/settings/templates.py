"""Template engine configuration."""

from .base import BASE_DIR, DEBUG

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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
            # Cache compiled templates for better performance (disabled in DEBUG mode)
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                ),
            ]
            if not DEBUG
            else [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        },
    },
]
