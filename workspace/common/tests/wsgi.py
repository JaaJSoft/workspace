"""Call a raw WSGI app from a test the way Django's test client calls a view.

A WSGI app served outside Django's handler sends ``request_started`` and
``request_finished`` itself, as Django's handler does, and
``close_old_connections`` is connected to both. Inside a ``TestCase`` the
request runs on the connection holding the test's transaction, where
autocommit is off, so that receiver closes it: the next query raises "Cannot
open a new connection in an atomic block". In-memory SQLite ignores the close,
which is why only PostgreSQL shows it.

Django's test client disconnects the receiver for the length of a request;
``served_in_test`` does the same around any WSGI callable.
"""

from django.core.signals import request_finished, request_started
from django.db import close_old_connections


class served_in_test:  # noqa: N801 - reads as a function at the call site
    """Wrap *app* so its requests leave the test's connection open.

    Attribute reads pass through, so a test can still reach the wrapped app's
    own attributes (``.provider_map``, ``.config``).
    """

    def __init__(self, app):
        self._app = app

    def __call__(self, environ, start_response):
        request_started.disconnect(close_old_connections)
        request_finished.disconnect(close_old_connections)
        try:
            yield from self._app(environ, start_response)
        finally:
            request_started.connect(close_old_connections)
            request_finished.connect(close_old_connections)

    def __getattr__(self, name):
        return getattr(self._app, name)
