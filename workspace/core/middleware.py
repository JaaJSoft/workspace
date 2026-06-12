from django.http import HttpResponseForbidden, JsonResponse
from django.template.loader import render_to_string

from workspace.core.services.module_access import (
    can_access_module,
    module_slug_from_dotted_path,
    restrictable_module_slugs,
)


class ModuleAccessMiddleware:
    """Block requests to modules the authenticated user may not access.

    Runs in ``process_view`` so Django has already resolved the URL; the view's
    defining module (``workspace.<slug>.*``) yields the module slug without a
    duplicated URL-prefix table. Non-restrictable modules and anonymous
    requests pass through untouched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        slug = module_slug_from_dotted_path(getattr(view_func, "__module__", ""))
        if slug is None or slug not in restrictable_module_slugs():
            return None
        if can_access_module(user, slug):
            return None
        return self._forbidden(request)

    @staticmethod
    def _is_ajax(request):
        return bool(
            request.headers.get("X-Alpine-Request")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )

    @classmethod
    def _forbidden(cls, request):
        if request.path.startswith("/api/"):
            return JsonResponse({"detail": "Module not available."}, status=403)
        if cls._is_ajax(request):
            return HttpResponseForbidden("Module not available.")
        html = render_to_string("403.html", request=request)
        return HttpResponseForbidden(html)
