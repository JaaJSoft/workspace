"""Installed applications."""

from .base import DEBUG, TESTING

INSTALLED_APPS = [
    "django_daisy",
    "django.contrib.admin",
    "django.contrib.humanize",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "knox",
    "mozilla_django_oidc",
    "django_filters",
    "django_prometheus",
    "django_http_compression",
    # Workspace apps
    "workspace.core",
    "workspace.common",
    "workspace.files",
    "workspace.files.ui",
    "workspace.notes",
    "workspace.notes.ui",
    "workspace.projects",
    "workspace.projects.ui",
    "workspace.dashboard",
    "workspace.users",
    "workspace.users.ui",
    "workspace.chat",
    "workspace.chat.ui",
    "workspace.calendar",
    "workspace.calendar.ui",
    "workspace.mail",
    "workspace.mail.ui",
    "workspace.notifications",
    "workspace.ai",
]

# Add Debug Toolbar only in DEBUG mode and not during tests
if DEBUG and not TESTING:
    INSTALLED_APPS.insert(
        INSTALLED_APPS.index("django.contrib.staticfiles") + 1, "debug_toolbar"
    )
