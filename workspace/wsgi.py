"""
WSGI config for workspace project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os
import threading

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace.settings')

_django_app = get_wsgi_application()

_webdav_app = None
_webdav_lock = threading.Lock()

DAV_PREFIX = "/dav"


def _get_webdav_app():
    global _webdav_app
    if _webdav_app is None:
        with _webdav_lock:
            if _webdav_app is None:
                from workspace.files.webdav.app import create_webdav_app
                _webdav_app = create_webdav_app()
    return _webdav_app


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")

    if path == DAV_PREFIX or path.startswith(DAV_PREFIX + "/"):
        # Strip the /dav prefix so WsgiDAV sees paths relative to its root.
        environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + DAV_PREFIX
        environ["PATH_INFO"] = path[len(DAV_PREFIX):] or "/"
        return _get_webdav_app()(environ, start_response)

    return _django_app(environ, start_response)
