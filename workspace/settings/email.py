"""Mail the instance sends on its own behalf (workspace.core.services.email).

Unrelated to the mail module, which sends as the user through the user's own
account. Everything here is the instance's identity: its relay, its From
address, its sending limits.

With nothing configured, sending is off: the backend is Django's dummy one and
EMAIL_ENABLED is false, so every call to the service is a logged no-op and a
stray ``send_mail`` reaches nobody. Setting EMAIL_HOST (or an explicit
EMAIL_BACKEND) turns it on. Development falls back to the console backend.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import DEBUG, TESTING
from .env import env_bool, env_list

_SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
_CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"
_DUMMY_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

EMAIL_HOST = os.getenv("EMAIL_HOST", "").strip()
_explicit_backend = os.getenv("EMAIL_BACKEND", "").strip()

if _explicit_backend:
    EMAIL_BACKEND = _explicit_backend
elif EMAIL_HOST:
    EMAIL_BACKEND = _SMTP_BACKEND
elif DEBUG and not TESTING:
    EMAIL_BACKEND = _CONSOLE_BACKEND
else:
    EMAIL_BACKEND = _DUMMY_BACKEND

# Whether the service sends at all. Derived from the backend so a deployment
# only has to point at a relay; set it to false to keep a relay configured
# while muting the instance.
EMAIL_ENABLED = env_bool("EMAIL_ENABLED", default=EMAIL_BACKEND != _DUMMY_BACKEND)

# 587 with STARTTLS is what every relay accepts for authenticated submission.
# Implicit TLS (EMAIL_USE_SSL, usually port 465) turns STARTTLS off.
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=not EMAIL_USE_SSL)
if EMAIL_USE_SSL and EMAIL_USE_TLS:
    raise ImproperlyConfigured(
        "EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive: "
        "STARTTLS on 587 or implicit TLS on 465, not both"
    )
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "465" if EMAIL_USE_SSL else "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
# Seconds per SMTP operation. Delivery runs in a Celery task, so this bounds
# how long a dead relay holds a worker, not how long a request waits.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "15"))

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Workspace <noreply@localhost>")
# Django's own error mails (mail_admins). Kept apart from DEFAULT_FROM_EMAIL so
# the operator's stream never shares a reputation with the users' one.
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
# Where a reply lands. Empty sends none, and a reply goes to the From address.
EMAIL_REPLY_TO = env_list("EMAIL_REPLY_TO")

# Public origin of the instance, e.g. https://workspace.example.com. Links in a
# mail (unsubscribe, preferences) cannot be derived from a request, since
# delivery runs in a worker. Without it only transactional mail is sent: a
# notification without a working unsubscribe link is exactly what a mailbox
# provider files as spam.
EMAIL_BASE_URL = os.getenv("EMAIL_BASE_URL", "").strip().rstrip("/")

# Sends accepted per rolling hour, per recipient address and for the whole
# instance. A caller stuck in a loop hits these before it hits anyone's inbox.
EMAIL_RATE_LIMIT_PER_RECIPIENT = int(os.getenv("EMAIL_RATE_LIMIT_PER_RECIPIENT", "20"))
EMAIL_RATE_LIMIT_GLOBAL = int(os.getenv("EMAIL_RATE_LIMIT_GLOBAL", "500"))

# Shared secret of the bounce and complaint webhook (POST /api/v1/email/bounces).
# Empty disables the endpoint.
EMAIL_BOUNCE_WEBHOOK_TOKEN = os.getenv("EMAIL_BOUNCE_WEBHOOK_TOKEN", "")

# Days a send record is kept. Long enough to answer "why did I get this mail"
# about last month; suppressions are never purged.
EMAIL_DELIVERY_RETENTION_DAYS = int(os.getenv("EMAIL_DELIVERY_RETENTION_DAYS", "90"))
