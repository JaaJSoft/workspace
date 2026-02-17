from workspace.users import presence_service


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
