"""Request-time enforcement of the preview audience.

A preview module outside the user's ``PREVIEW_VISIBILITY`` audience answers
the way an unmatched URL does - same status, same page, same headers - so the
setting cannot be used to learn which modules exist. Ownership comes from where
a view's code lives (``owning_module``), so a new view is guarded without
declaring anything.

``PreviewModuleMiddleware`` judges the session user, on pages and API views
alike, before the CSRF check: that check answers 403 to a tokenless write, and
an unmatched URL never reaches it. A caller authenticated otherwise - a token,
basic auth - is only known once DRF has authenticated it, so ``ModuleVisible``
judges that one.

``rest_framework.views`` is imported inside the functions that need it: DRF
loads ``DEFAULT_PERMISSION_CLASSES`` - this module - while that module is still
being imported, so a top-level import is circular.
"""

from django.contrib.auth import get_user
from django.http import Http404
from rest_framework.permissions import BasePermission

from .services.module_visibility import owning_module, user_can_see_module


class HiddenModule(Http404):
    """Raised for a module the user may not see; rendered as a plain 404."""


def is_hidden_from(user, dotted_path):
    module = owning_module(dotted_path)
    return module is not None and not user_can_see_module(user, module)


class PreviewModuleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # An anonymous visitor is left to the view's own login redirect, and
        # a token caller - anonymous to the session - to ModuleVisible.
        if not request.user.is_authenticated:
            return None
        if is_hidden_from(request.user, view_func.__module__):
            raise HiddenModule
        return None


class ModuleVisible(BasePermission):
    """Refuses, as a 404, an API view of a module the caller may not see.

    An anonymous caller is left to ``IsAuthenticated``, as on a page.
    """

    def has_permission(self, request, view):
        if request.user.is_authenticated and is_hidden_from(
            request.user, type(view).__module__
        ):
            raise HiddenModule
        return True


def exception_handler(exc, context):
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, HiddenModule):
        # Returning None makes DRF re-raise, so Django's own 404 handling
        # renders the answer an unmatched URL gets - which never reaches DRF.
        # DRF has already written the authenticated caller onto the Django
        # request; put back the user the session names, the one an unmatched
        # URL renders with, or a token caller's 404 would greet them by name.
        django_request = context["request"]._request
        django_request.user = get_user(django_request)
        return None
    return drf_exception_handler(exc, context)
