"""Middleware stack."""

from .base import DEBUG, TESTING

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # HTTP conditional GET support (ETags & Last-Modified headers for browser caching)
    "django.middleware.http.ConditionalGetMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "workspace.users.middleware.TimezoneMiddleware",
    "workspace.users.middleware.AjaxLoginRedirectMiddleware",
    "workspace.users.middleware.PresenceMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_http_compression.middleware.HttpCompressionMiddleware",
    # Mesure du temps de traitement pour affichage dans le footer UI et header HTTP
    # 'Workspace.common.middleware.RequestTimingMiddleware',
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

# Add Debug Toolbar middleware only in DEBUG mode and not during tests
if DEBUG and not TESTING:
    compression_idx = MIDDLEWARE.index(
        "django_http_compression.middleware.HttpCompressionMiddleware"
    )
    MIDDLEWARE.insert(
        compression_idx + 1, "debug_toolbar.middleware.DebugToolbarMiddleware"
    )
