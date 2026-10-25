import gettext as gettext_module

from django.apps import apps
from django.conf import settings
from django.http import Http404
from django.utils import translation
from django.utils.cache import patch_cache_control
from django.views.i18n import JavaScriptCatalog

from workspace.core.services.translations import shared_locale_dir


class WorkspaceJavaScriptCatalog(JavaScriptCatalog):
    """The djangojs catalog of one language, as the gettext() globals scripts call.

    The language comes from the URL, not from the request, so a response is a
    function of its URL and a browser may cache it: the shell links the
    catalog of the language it activated, with the app version as a cache
    buster for deploys.

    Only the workspace apps' catalogs are read. Django's own djangojs catalogs
    (the admin's) translate msgids such as "Today" in their own words, and
    would shadow ours whenever INSTALLED_APPS lists them first.

    The shared module's server-side catalog is served too: the words every
    module uses are translated once, for templates and scripts alike.
    """

    def get(self, request, *args, language, **kwargs):
        if language not in dict(settings.LANGUAGES):
            raise Http404
        self.packages = [
            app.name
            for app in apps.get_app_configs()
            if app.name.startswith("workspace.")
        ]
        with translation.override(language):
            response = super().get(request, *args, **kwargs)
        response["Content-Language"] = language
        if settings.DEBUG:
            patch_cache_control(response, no_cache=True)
        else:
            patch_cache_control(response, public=True, max_age=86400)
        return response

    def get_catalog(self):
        shared = gettext_module.translation(
            "django",
            localedir=shared_locale_dir(),
            languages=[translation.to_locale(translation.get_language())],
            fallback=True,
        )
        catalog = self.translation._catalog
        for key, value in getattr(shared, "_catalog", {}).items():
            if key and key not in catalog:
                catalog[key] = value
        return super().get_catalog()
