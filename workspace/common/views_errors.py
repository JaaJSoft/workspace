"""Custom error handlers."""

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string


def handler429(request, exception):
    """Handle 429 Too Many Requests — HTML or JSON based on Accept header."""
    accept = request.META.get('HTTP_ACCEPT', '')
    if 'application/json' in accept:
        return JsonResponse(
            {'detail': 'Too many requests. Please try again later.'},
            status=429,
        )
    html = render_to_string('429.html', request=request)
    return HttpResponse(html, status=429, content_type='text/html')
