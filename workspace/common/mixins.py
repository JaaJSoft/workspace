class CacheControlMixin:
    """Inject Cache-Control headers on DRF responses.

    Defaults to ``private, max-age=0, must-revalidate`` which forces the
    browser to revalidate via ETag on every request (ConditionalGetMiddleware
    handles the 304).  Override *cache_max_age* on a view to let the client
    serve from cache without contacting the server at all.

    Attributes:
        cache_max_age: Seconds the client may cache the response before
            revalidating. ``0`` means "always revalidate".
        cache_private: When ``True`` (default), the response is only
            cacheable by the end-user's browser (``private``).  Set to
            ``False`` for public/unauthenticated endpoints so CDNs and
            shared proxies may also cache.
    """

    cache_max_age = 0
    cache_private = True

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)

        # Don't touch errors or responses that already have the header
        if response.status_code >= 400 or response.get('Cache-Control'):
            return response

        visibility = 'private' if self.cache_private else 'public'
        if self.cache_max_age > 0:
            response['Cache-Control'] = f'{visibility}, max-age={self.cache_max_age}'
        else:
            response['Cache-Control'] = f'{visibility}, max-age=0, must-revalidate'

        return response
