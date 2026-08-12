"""Project-wide HTTP middleware."""


class HtmlCacheControlMiddleware:
    """Set ``Cache-Control: private, no-cache`` on HTML responses.

    Without an explicit policy, freshness is left to per-browser heuristics
    (RFC 9111 section 4.2.2). ``no-cache`` means "store, but revalidate
    before reuse", so repeat navigations hit the ETag path and cost a 304.
    Never use ``no-store`` here: it disables the bfcache and makes
    ConditionalGetMiddleware skip the ETag entirely.

    Responses that already declare a policy (thumbnails, SSE, the API's
    CacheControlMixin) are returned unchanged, as are non-HTML responses.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get("Cache-Control"):
            return response
        if response.get("Content-Type", "").startswith("text/html"):
            response["Cache-Control"] = "private, no-cache"
        return response
