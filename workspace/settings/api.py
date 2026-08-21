"""REST framework, OpenAPI schema and API token authentication."""

from .base import APP_VERSION, DEBUG
from .env import env_non_negative_int

# Hops between the client and this application, for the per-IP rate limits.
# Left unset, X-Forwarded-For is ignored and the peer address is used: the
# header is written by the caller, so believing it hands out a fresh bucket per
# request and the limit stops existing. Set it only when a proxy chain of that
# length is in front AND overwrites rather than appends to the header.
_NUM_PROXIES = env_non_negative_int("NUM_PROXIES")

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Disable BrowsableAPI renderer in production for better performance
    "DEFAULT_RENDERER_CLASSES": [
        "drf_orjson_renderer.renderers.ORJSONRenderer",
    ]
    if not DEBUG
    else [
        "drf_orjson_renderer.renderers.ORJSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "NUM_PROXIES": _NUM_PROXIES,
    # Vault account endpoints. The design's v1 starting values; retune on
    # telemetry, and raise rather than lower - each one guards key material.
    "DEFAULT_THROTTLE_RATES": {
        "vault.account.init.ip": "10/min",
        "vault.account.init.user": "30/hour",
        "vault.account.finalize.ip": "10/min",
        "vault.account.envelope.burst": "10/min",
        "vault.account.envelope.user": "60/hour",
        # Deliberately not redundant with the per-user limit above: it catches
        # an exfiltration spread across several stolen session cookies.
        "vault.account.envelope.ip": "200/hour",
        "vault.account.rotate.user": "5/hour",
    },
    "DEFAULT_PARSER_CLASSES": [
        "drf_orjson_renderer.parsers.ORJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Workspace API",
    "DESCRIPTION": (
        "Workspace productivity suite for organizing and managing daily work."
    ),
    "VERSION": APP_VERSION,
    # Ensure file uploads are correctly rendered as multipart/form-data in Swagger UI
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/v[1-9]",
    "TAGS": [
        {
            "name": "Auth",
            "description": "API token management for programmatic access.",
        },
        {
            "name": "AI",
            "description": "AI-powered tasks: summarize, compose, reply, and editor actions.",
        },
        {
            "name": "Calendar",
            "description": "Calendars and events.",
        },
        {
            "name": "Calendar - Polls",
            "description": "Scheduling polls for finding the best meeting time.",
        },
        {
            "name": "Calendar - Polls (Public)",
            "description": "Public endpoints for guest poll participation.",
        },
        {
            "name": "Chat",
            "description": "Real-time messaging with direct and group conversations.",
        },
        {
            "name": "Files",
            "description": "Browse and manage files and folders.",
        },
        {
            "name": "Thumbnails",
            "description": "File thumbnail generation.",
        },
        {
            "name": "Mail",
            "description": "Read and send emails from external mail accounts.",
        },
        {
            "name": "Modules",
            "description": "Workspace module registry.",
        },
        {
            "name": "Notifications",
            "description": "User notifications and push subscriptions.",
        },
        {
            "name": "Projects",
            "description": "Collaborative projects with kanban boards and backlogs.",
        },
        {
            "name": "Search",
            "description": "Unified search across workspace modules.",
        },
        {
            "name": "Settings",
            "description": "Per-user, per-module key-value settings.",
        },
        {
            "name": "Users",
            "description": "User profiles, avatars, passwords, and presence status.",
        },
        {
            "name": "Vault",
            "description": "End-to-end encrypted password vault (preview).",
        },
    ],
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
    },
    "SERVE_AUTHENTICATION": [
        "rest_framework.authentication.SessionAuthentication",
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
}

# Knox (API token authentication)
REST_KNOX = {
    "TOKEN_TTL": None,  # No default expiry; per-token expiry set at creation
    "AUTO_REFRESH": False,
    "AUTH_HEADER_PREFIX": "Token",
}
KNOX_TOKEN_MODEL = "knox.AuthToken"
