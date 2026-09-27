"""REST framework, OpenAPI schema and API token authentication."""

import orjson

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
    # A view that overrides permission_classes must keep ModuleVisible, or a
    # preview module's endpoint answers users outside its audience;
    # core.tests.test_module_guard fails on a preview module that drops it.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "workspace.core.module_guard.ModuleVisible",
    ],
    "EXCEPTION_HANDLER": "workspace.core.module_guard.exception_handler",
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
        # Browser-posted Content-Security-Policy reports. Not one of the
        # account limits above: it guards no key material, and refusing a
        # report protects nothing - it only keeps a page stuck in a violation
        # loop from turning the log into noise. What it costs is the mirror
        # of that. Behind a reverse proxy with NUM_PROXIES unset, the bucket
        # is shared by the whole deployment, so ordinary traffic can exhaust
        # it and anyone able to reach the endpoint can exhaust it on purpose;
        # reports refused past the limit are dropped silently on both sides,
        # which is monitoring going dark with no symptom. Set NUM_PROXIES.
        # The value is a v1 starting point like the ones above - retune it
        # on telemetry from a real deployment rather than on a guess here.
        "core.csp_report.ip": "30/min",
    },
    "DEFAULT_PARSER_CLASSES": [
        "drf_orjson_renderer.parsers.ORJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    # A nested many=True serializer field reports its per-item errors as a
    # dict keyed by list index (DRF's LIST_SERIALIZER_ERRORS_AS_DICT default).
    # orjson refuses non-str dict keys unless told to coerce them.
    "ORJSON_RENDERER_OPTIONS": (orjson.OPT_NON_STR_KEYS,),
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
            "name": "People",
            "description": "Personal and group address book.",
        },
        {
            "name": "Photos - Albums",
            "description": "Albums gathering photos and videos from the library.",
        },
        {
            "name": "Photos - Faces",
            "description": "Face grouping: the people found in your photos, and their corrections.",
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
            "name": "Projects - Epics",
            "description": "Epics grouping tasks into larger initiatives, with progress rollup.",
        },
        {
            "name": "Projects - Sprints",
            "description": "Timeboxed sprints of scrum projects: planning, start and completion.",
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
