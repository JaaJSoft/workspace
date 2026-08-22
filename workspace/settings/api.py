"""REST framework, OpenAPI schema and API token authentication."""

from .base import APP_VERSION, DEBUG

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
    "SERVE_INCLUDE_SCHEMA": False,
    "TAGS": [
        {
            "name": "Activity",
            "description": "Cross-module activity feed and usage statistics.",
        },
        {
            "name": "AI",
            "description": "AI-powered tasks: summarize, compose, reply, and editor actions.",
        },
        {
            "name": "Auth",
            "description": "API token management for programmatic access.",
        },
        {
            "name": "Calendar",
            "description": "Calendars and events.",
        },
        {
            "name": "Calendar - External",
            "description": "Subscriptions to external ICS calendar feeds.",
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
            "name": "Chat - Conversations",
            "description": "Direct and group conversations: membership, read state, pins, avatars, schedules, and goals.",
        },
        {
            "name": "Chat - Messages",
            "description": "Messages within a conversation: sending, editing, reactions, pins, threads, and search.",
        },
        {
            "name": "Chat - Calls",
            "description": "Audio/video call signaling and state.",
        },
        {
            "name": "Chat - Attachments",
            "description": "Message attachments and the conversation media gallery.",
        },
        {
            "name": "Files",
            "description": "Browse and manage files and folders.",
        },
        {
            "name": "Files - Tags",
            "description": "File tags and their assignment to files.",
        },
        {
            "name": "Files - Shared Links",
            "description": "Public share links for files.",
        },
        {
            "name": "Files - Thumbnails",
            "description": "File thumbnail generation.",
        },
        {
            "name": "Imports",
            "description": "Import data from other clouds: connections and import jobs.",
        },
        {
            "name": "Mail - Accounts",
            "description": "External mail accounts: setup, OAuth2, autodiscover, and sync.",
        },
        {
            "name": "Mail - Messages",
            "description": "Reading, sending, and drafting emails, with attachments and contact autocomplete.",
        },
        {
            "name": "Mail - Folders & Labels",
            "description": "Mailbox folders and user-defined labels.",
        },
        {
            "name": "Mail - Rules",
            "description": "Automatic filing rules for incoming mail.",
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
            "description": "Collaborative projects: settings, members, archiving, and actions.",
        },
        {
            "name": "Projects - Tasks",
            "description": "Tasks with subtasks, comments, attachments, links, search, and calendar feed.",
        },
        {
            "name": "Projects - Statuses & Labels",
            "description": "Kanban board statuses and task labels.",
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
