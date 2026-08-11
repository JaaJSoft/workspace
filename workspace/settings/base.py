"""Core Django settings: paths, debug flags, hosts, URLs, i18n.

Every other settings module builds on the flags defined here.
"""

import mimetypes
import os
import sys
from pathlib import Path

from .env import env_bool, env_list

# Fix MIME types for JavaScript modules on Windows
mimetypes.add_type("application/javascript", ".js", True)
mimetypes.add_type("application/javascript", ".mjs", True)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv(
    "SECRET_KEY", "django-insecure-(apvd+h#1t_@zz504ks3ek2q_4*wm!p+#!(vte70q*xru47-zj"
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DEBUG", default=True)

# True while running the test suite: disables the debug toolbar and swaps in
# cheaper password hashing / a file-backed test database.
TESTING = "test" in sys.argv

# ALLOWED_HOSTS configurable via comma-separated env; default allows all in container use
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS") or (["*"] if not DEBUG else [])

ROOT_URLCONF = "workspace.urls"

# Disable automatic slash appending to URLs
APPEND_SLASH = False

WSGI_APPLICATION = "workspace.wsgi.application"

# Application version (from env, defaults to 'dev')
APP_VERSION = os.getenv("APP_VERSION") or "dev"

# Audience that may see modules flagged preview=True on the home page, nav,
# command palette and search. One of: all, staff, admin, none (default staff).
# Validated/normalized at read time in workspace.core.services.module_visibility.
PREVIEW_VISIBILITY = os.getenv("PREVIEW_VISIBILITY", "staff")

# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
