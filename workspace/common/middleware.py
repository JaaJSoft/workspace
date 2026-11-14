"""Project-wide HTTP middleware."""

from .prefetch import is_prefetchable, refused_speculation, speculative_purpose


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


class PrefetchGateMiddleware:
    """Refuse a speculative request for a view that did not opt in.

    A prefetch (``Sec-Purpose: prefetch``) runs the view before anyone decided
    to open the page, so only views marked ``@prefetchable`` answer it. A
    prerender also runs the page's scripts, which no view vouches for: it is
    always refused.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        purpose = speculative_purpose(request)
        if purpose is None:
            return None
        if "prerender" in purpose or not is_prefetchable(view_func):
            return refused_speculation()
        return None
