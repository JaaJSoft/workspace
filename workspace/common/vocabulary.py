"""Interface words every module uses, translated once in common's catalog.

Nothing imports this module: it exists for extraction. ``manage.py
translations`` reads these msgids into common's catalog and leaves them out of
every other module's, so ``{% translate "Cancel" %}`` in any template finds the
one translation here. A word whose meaning depends on the module (a "Share"
that is a noun on one page and a verb on another) takes a ``context`` at its
call site instead of joining this list.
"""

from django.utils.translation import gettext_noop

VOCABULARY = [
    gettext_noop("Back"),
    gettext_noop("Cancel"),
    gettext_noop("Close"),
    gettext_noop("Confirm"),
    gettext_noop("Copy"),
    gettext_noop("Delete"),
    gettext_noop("Download"),
    gettext_noop("Edit"),
    gettext_noop("Loading…"),
    gettext_noop("Next"),
    gettext_noop("Previous"),
    gettext_noop("Refresh"),
    gettext_noop("Reload"),
    gettext_noop("Rename"),
    gettext_noop("Retry"),
    gettext_noop("Save"),
    gettext_noop("Search"),
]
