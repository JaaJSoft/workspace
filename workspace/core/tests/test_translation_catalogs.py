"""The committed gettext catalogs: compiled, complete enough to load, consistent.

Django reads the .mo files, translators and reviewers read the .po files, and
nothing at runtime notices when the two drift: a .po edited without running
the translations command ships the old strings, silently.
"""

import gettext
import re
from pathlib import Path

import polib
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.utils import translation

from workspace.core.services.translations import shared_catalogs
from workspace.core.views.i18n import WorkspaceJavaScriptCatalog

WORKSPACE_DIR = Path(settings.BASE_DIR) / "workspace"


def po_files(domain="*"):
    return sorted(WORKSPACE_DIR.glob(f"*/locale/*/LC_MESSAGES/{domain}.po"))


def mo_catalog(path):
    with path.open("rb") as fh:
        catalog = gettext.GNUTranslations(fh)._catalog
    catalog.pop("", None)
    return catalog


def po_catalog(po):
    """What msgfmt compiles out of a .po: its translated, non-fuzzy entries."""
    catalog = {}
    for entry in po.translated_entries():
        msgid = msgid_key(entry)
        if entry.msgid_plural:
            for index, msgstr in entry.msgstr_plural.items():
                catalog[(msgid, int(index))] = msgstr
        else:
            catalog[msgid] = entry.msgstr
    return catalog


def msgid_key(entry):
    return f"{entry.msgctxt}\x04{entry.msgid}" if entry.msgctxt else entry.msgid


def expected(entry):
    """The entry's msgstr; for a plural, the form of n=1 (index 0 in French)."""
    return entry.msgstr_plural[0] if entry.msgid_plural else entry.msgstr


def server_translation(entry):
    """What templates and Python get for the entry under the active language."""
    if entry.msgid_plural and entry.msgctxt:
        return translation.npgettext(entry.msgctxt, entry.msgid, entry.msgid_plural, 1)
    if entry.msgid_plural:
        return translation.ngettext(entry.msgid, entry.msgid_plural, 1)
    if entry.msgctxt:
        return translation.pgettext(entry.msgctxt, entry.msgid)
    return translation.gettext(entry.msgid)


class _CapturedScriptCatalog(WorkspaceJavaScriptCatalog):
    def render_to_response(self, context, **response_kwargs):
        response = HttpResponse()
        response.catalog = context["catalog"]
        return response


def served_script_catalog(language):
    """The msgid -> msgstr mapping the JavaScript catalog view sends."""
    view = _CapturedScriptCatalog.as_view()
    return view(RequestFactory().get("/"), language=language).catalog


def script_translation(catalog, entry):
    value = catalog.get(msgid_key(entry))
    return value[0] if entry.msgid_plural and value else value


class TranslationCatalogTests(SimpleTestCase):
    def test_some_catalog_exists(self):
        # Guards the glob below: a moved locale directory would otherwise
        # turn every other test here into a pass over nothing.
        self.assertTrue(po_files())

    def test_every_catalog_is_compiled_from_its_po(self):
        for po_path in po_files():
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                mo_path = po_path.with_suffix(".mo")
                self.assertTrue(
                    mo_path.exists(),
                    "Run `manage.py translations --compile-only` and commit the .mo.",
                )
                self.assertEqual(
                    mo_catalog(mo_path),
                    po_catalog(polib.pofile(str(po_path))),
                    "The .mo is stale: run `manage.py translations --compile-only`.",
                )

    def test_every_catalog_is_for_an_offered_language(self):
        offered = {code for code, _name in settings.LANGUAGES} - {
            settings.LANGUAGE_CODE
        }
        for po_path in po_files():
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                self.assertIn(po_path.parent.parent.name, offered)

    def test_header_is_not_fuzzy(self):
        # msgfmt skips a fuzzy header, Plural-Forms with it: every plural
        # would then fall back to the English rule.
        for po_path in po_files():
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                po = polib.pofile(str(po_path))
                self.assertFalse(po.metadata_is_fuzzy)
                self.assertIn("Plural-Forms", po.metadata)

    def test_no_fuzzy_entry(self):
        # A fuzzy entry is a guess msgmerge made from a similar msgid; msgfmt
        # leaves it out, so it reads as translated in the .po and is not.
        for po_path in po_files():
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                fuzzy = [
                    entry.msgid for entry in polib.pofile(str(po_path)).fuzzy_entries()
                ]
                self.assertEqual(fuzzy, [])

    def test_plural_entries_fill_every_form_of_the_language(self):
        # makemessages writes two msgstr[] slots whatever Plural-Forms says.
        # A missing form compiles fine and falls back to English for every
        # count that selects it (French: 3 files, 12 files...).
        for po_path in po_files():
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                po = polib.pofile(str(po_path))
                nplurals = int(
                    re.search(r"nplurals=(\d+)", po.metadata["Plural-Forms"]).group(1)
                )
                incomplete = [
                    entry.msgid
                    for entry in po.translated_entries()
                    if entry.msgid_plural and len(entry.msgstr_plural) < nplurals
                ]
                self.assertEqual(incomplete, [])

    def test_no_catalog_repeats_a_shared_msgid(self):
        # The shared module translates the words every module uses once; a
        # catalog that carries one too lets the two translations drift.
        for po_path in po_files():
            module_dir = WORKSPACE_DIR / po_path.relative_to(WORKSPACE_DIR).parts[0]
            shared = {
                (entry.msgctxt, entry.msgid)
                for catalog in shared_catalogs(module_dir, po_path.stem)
                if catalog.parent.parent == po_path.parent.parent
                for entry in polib.pofile(str(catalog))
            }
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                repeated = [
                    entry.msgid
                    for entry in polib.pofile(str(po_path))
                    if (entry.msgctxt, entry.msgid) in shared
                ]
                self.assertEqual(
                    repeated,
                    [],
                    "Run `manage.py translations <module>`: it drops shared msgids.",
                )

    def test_no_server_translation_is_shadowed(self):
        # Every installed app's catalog is merged into one, and an app listed
        # earlier wins a msgid both hold: Django's and DRF's catalogs read
        # "Page not found" as "Page non trouvée" and "Created" as "Création",
        # whatever ours says. The cure is a context at the call site.
        for po_path in po_files("django"):
            language = po_path.parent.parent.name
            with (
                self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))),
                translation.override(language),
            ):
                shadowed = [
                    (entry.msgctxt, entry.msgid, got)
                    for entry in polib.pofile(str(po_path)).translated_entries()
                    if (got := server_translation(entry)) != expected(entry)
                ]
                self.assertEqual(shadowed, [])

    def test_no_script_translation_is_shadowed(self):
        # The browser reads the catalog the view serves, not the merged one:
        # check each script msgid there, against the same precedence.
        for po_path in po_files("djangojs"):
            language = po_path.parent.parent.name
            with self.subTest(po=str(po_path.relative_to(settings.BASE_DIR))):
                catalog = served_script_catalog(language)
                shadowed = [
                    (entry.msgctxt, entry.msgid, got)
                    for entry in polib.pofile(str(po_path)).translated_entries()
                    if (got := script_translation(catalog, entry)) != expected(entry)
                ]
                self.assertEqual(shadowed, [])
