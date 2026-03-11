from django.conf import settings
from django.http import HttpResponse

from workspace.users import presence_service


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
            and response.get('Location', '').startswith(settings.LOGIN_URL)
            and self._is_ajax(request)
        ):
            return HttpResponse('login_required', status=401)
        return response

    @staticmethod
    def _is_ajax(request):
        return (
            request.headers.get('X-Alpine-Request')
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )


class PresenceMiddleware:
    """Update user presence on every authenticated request (except SSE streams)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            hasattr(request, 'user')
            and request.user.is_authenticated
            and not getattr(request, '_is_sse_stream', False)
        ):
            presence_service.touch(request.user.id)
        return response
