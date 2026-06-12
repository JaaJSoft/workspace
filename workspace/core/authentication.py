from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.exceptions import PermissionDenied

from workspace.core.services.module_access import (
    can_access_module,
    module_slug_from_dotted_path,
    restrictable_module_slugs,
)


def enforce_module_access(request, user):
    """Raise PermissionDenied if *user* may not access the resolved view's module.

    Runs after DRF authentication, so it covers token and basic auth that the
    session-based middleware cannot see. Resolves the target module from the
    already-routed view (``request.resolver_match``), so per-view
    ``permission_classes`` overrides cannot bypass it.
    """
    if user is None or not user.is_authenticated:
        return
    resolver_match = getattr(request, "resolver_match", None)
    view_func = getattr(resolver_match, "func", None) if resolver_match else None
    slug = module_slug_from_dotted_path(getattr(view_func, "__module__", ""))
    if slug is None or slug not in restrictable_module_slugs():
        return
    if not can_access_module(user, slug):
        raise PermissionDenied("Module not available.")


class ModuleAccessSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            enforce_module_access(request, result[0])
        return result


class ModuleAccessTokenAuthentication(KnoxTokenAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            enforce_module_access(request, result[0])
        return result


class ModuleAccessBasicAuthentication(BasicAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            enforce_module_access(request, result[0])
        return result
