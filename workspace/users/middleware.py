from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone, translation

from workspace.users.services import presence as presence_service
from workspace.users.services.settings import get_user_language, get_user_timezone


class AjaxLoginRedirectMiddleware:
    """Return 401 instead of 302-to-login for AJAX/async requests.

    When a session expires, Django's @login_required returns a 302 redirect
    to the login page.  For normal page loads this is fine, but for AJAX
    requests (alpine-ajax, fetch, XHR) the browser silently follows the
    redirect and injects the login page HTML into the partial container.

    This middleware intercepts those redirects and returns a 401 so the
    frontend can detect it and redirect the whole page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.status_code == 302
            and response.get("Location", "").startswith(settings.LOGIN_URL)
            and self._is_ajax(request)
        ):
            return HttpResponse("login_required", status=401)
        return response

    @staticmethod
    def _is_ajax(request):
        return (
            request.headers.get("X-Alpine-Request")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )


class TimezoneMiddleware:
    """Activate the user's configured timezone for the duration of the request.

    Everything downstream (|date / |timesince filters, localtime(),
    localdate(), __date ORM lookups) then resolves in that zone. The
    finally-deactivate keeps the zone from leaking to the next request
    served by the same worker thread. Streaming/SSE generators run after
    the deactivate and therefore stay in UTC - they only emit ISO strings.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            timezone.activate(get_user_timezone(request.user))
        else:
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()


class LanguageMiddleware:
    """Activate the user's interface language for the duration of the request.

    The language is the user's stored choice, never the browser's
    Accept-Language: the language picker in Settings is the only way in, so a
    French browser does not get Django's own French strings (dates, form and
    API errors) in the middle of a page whose text is still English. Anonymous
    pages render in LANGUAGE_CODE. Deactivated in the finally for the same
    reason as TimezoneMiddleware, and streaming bodies run in LANGUAGE_CODE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            translation.activate(get_user_language(request.user))
        else:
            translation.activate(settings.LANGUAGE_CODE)
        request.LANGUAGE_CODE = translation.get_language()
        try:
            response = self.get_response(request)
        finally:
            translation.deactivate()
        response.headers.setdefault("Content-Language", request.LANGUAGE_CODE)
        return response


class PresenceMiddleware:
    """Update user presence on every authenticated request.

    Skipped for SSE streams and for requests the page flags as sent while
    nobody was in front of it (``page_attention.js``): a tab left open keeps
    refreshing on its own, and counting that as activity would hold back web
    push on the user's other devices.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            hasattr(request, "user")
            and request.user.is_authenticated
            and not getattr(request, "_is_sse_stream", False)
            and not request.headers.get("X-Page-Unattended")
        ):
            presence_service.touch(request.user.id)
        return response
