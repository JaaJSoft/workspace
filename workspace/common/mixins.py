class CacheControlMixin:
    """Inject Cache-Control headers on DRF responses.

    Defaults to ``private, max-age=0, must-revalidate`` which forces the
    browser to revalidate via ETag on every request (ConditionalGetMiddleware
    handles the 304).  Override *cache_max_age* on a view to let the client
    serve from cache without contacting the server at all.

    For per-user binary resources (avatars, thumbnails), also set
    *cache_stale_while_revalidate* to enable RFC 5861 background
    revalidation: the browser paints the cached copy instantly and quietly
    re-fetches in the background, hitting the ETag-driven 304 short-circuit.

    Attributes:
        cache_max_age: Seconds the client may cache the response before
            revalidating. ``0`` means "always revalidate".
        cache_private: When ``True`` (default), the response is only
            cacheable by the end-user's browser (``private``).  Set to
            ``False`` for public/unauthenticated endpoints so CDNs and
            shared proxies may also cache.
        cache_stale_while_revalidate: Optional seconds beyond
            ``cache_max_age`` during which the cached copy may be served
            stale while the client revalidates in the background. ``None``
            (default) omits the directive.
    """

    cache_max_age = 0
    cache_private = True
    cache_stale_while_revalidate = None

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)

        # Don't touch errors or responses that already have the header
        if response.status_code >= 400 or response.get("Cache-Control"):
            return response

        visibility = "private" if self.cache_private else "public"
        if self.cache_max_age > 0:
            directive = f"{visibility}, max-age={self.cache_max_age}"
            if self.cache_stale_while_revalidate:
                directive += (
                    f", stale-while-revalidate={self.cache_stale_while_revalidate}"
                )
        else:
            # max-age=0 + SWR doesn't make sense: SWR needs a positive
            # max-age window to enter the "stale" state. Ignore SWR here.
            directive = f"{visibility}, max-age=0, must-revalidate"

        response["Cache-Control"] = directive
        return response
