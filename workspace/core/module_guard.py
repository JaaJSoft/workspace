"""Request-time enforcement of the preview audience.

A preview module outside the user's ``PREVIEW_VISIBILITY`` audience answers
the way an unmatched URL does - same status, same page, same headers - so the
setting cannot be used to learn which modules exist. Ownership comes from where
a view's code lives (``owning_module``), so a new view is guarded without
declaring anything.

Pages go through ``PreviewModuleMiddleware``: a session is the only way a page
authenticates. API views go through ``ModuleVisible``, which runs after DRF
authentication and therefore sees token and basic-auth callers too.
"""

from django.http import Http404
from rest_framework.views import APIView

from .services.module_visibility import owning_module, user_can_see_module


class HiddenModule(Http404):
    """Raised for a module the user may not see; rendered as a plain 404."""


def is_hidden_from(user, dotted_path):
    module = owning_module(dotted_path)
    return module is not None and not user_can_see_module(user, module)


def _is_api_view(view_func):
    view_class = getattr(view_func, "cls", None)
    return isinstance(view_class, type) and issubclass(view_class, APIView)


class PreviewModuleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # A DRF view authenticates on its own, after this runs, so the session
        # user seen here is not necessarily its caller: ModuleVisible judges
        # it. An anonymous visitor is left to the view's own login redirect.
        if _is_api_view(view_func) or not request.user.is_authenticated:
            return None
        if is_hidden_from(request.user, view_func.__module__):
            raise HiddenModule
        return None
