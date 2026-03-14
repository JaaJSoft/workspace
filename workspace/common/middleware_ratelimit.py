"""Middleware to catch Ratelimited exceptions and return proper 429 responses."""

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django_ratelimit.exceptions import Ratelimited


class RatelimitMiddleware:
    """Convert Ratelimited exceptions to 429 responses with Retry-After."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, Ratelimited):
            return None

        retry_after = 60  # seconds

        if request.path.startswith('/api/'):
            response = JsonResponse(
                {'detail': 'Too many requests. Please try again later.'},
                status=429,
            )
        else:
            html = render_to_string('429.html', request=request)
            response = HttpResponse(html, status=429, content_type='text/html')

        response['Retry-After'] = str(retry_after)
        return response
