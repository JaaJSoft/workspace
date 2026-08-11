"""Django Debug Toolbar configuration (only active in DEBUG mode).

The app and middleware entries themselves are added in apps.py / middleware.py,
where the lists they belong to are defined.
"""

import socket

from .base import DEBUG

if DEBUG:
    # Get local IP for Docker/VM compatibility
    hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS = (
        [
            "127.0.0.1",
            "::1",  # IPv6 localhost (when accessing via http://localhost)
            "0.0.0.0",
        ]
        + [ip[: ip.rfind(".")] + ".1" for ip in ips]
    )

    # Configuration panels to show
    DEBUG_TOOLBAR_PANELS = [
        "debug_toolbar.panels.history.HistoryPanel",
        "debug_toolbar.panels.versions.VersionsPanel",
        "debug_toolbar.panels.timer.TimerPanel",
        "debug_toolbar.panels.settings.SettingsPanel",
        "debug_toolbar.panels.headers.HeadersPanel",
        "debug_toolbar.panels.request.RequestPanel",
        "debug_toolbar.panels.sql.SQLPanel",  # Most important for viewing queries
        "debug_toolbar.panels.staticfiles.StaticFilesPanel",
        "debug_toolbar.panels.templates.TemplatesPanel",
        "debug_toolbar.panels.cache.CachePanel",
        "debug_toolbar.panels.signals.SignalsPanel",
        "debug_toolbar.panels.redirects.RedirectsPanel",
        # Disabled: causes "Another profiling tool is already active" errors with concurrent requests
        # 'debug_toolbar.panels.profiling.ProfilingPanel',
    ]

    DEBUG_TOOLBAR_CONFIG = {
        "SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG,
        "SHOW_COLLAPSED": False,  # Toolbar expanded by default to be more visible
    }
