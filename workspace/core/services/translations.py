"""Where the interface strings every module shares are translated."""

from pathlib import Path

from django.apps import apps

# The module whose catalogs hold the strings every module shares ("Cancel",
# "Close", the UI kit's own text). Its server-side catalog is also served to
# the browser, so one msgid covers templates, Python and JavaScript; the
# other modules leave those msgids out of their own catalogs.
SHARED_MODULE = "common"


def shared_locale_dir():
    return Path(apps.get_app_config(SHARED_MODULE).path) / "locale"


def shared_catalogs(module_dir, domain):
    """The shared catalogs whose msgids the *domain* catalog of *module_dir* leaves out.

    The browser reads the shared module's server-side catalog too, so a
    djangojs catalog also leaves out what that one holds - the shared
    module's own included.
    """
    locale_dir = module_dir.parent / SHARED_MODULE / "locale"
    domains = {"django"} if domain == "djangojs" else set()
    if module_dir.name != SHARED_MODULE:
        domains.add(domain)
    return sorted(
        catalog
        for name in domains
        for catalog in locale_dir.glob(f"*/LC_MESSAGES/{name}.po")
    )
