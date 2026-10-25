"""The committed gettext catalogs: compiled, complete enough to load, consistent.

Django reads the .mo files, translators and reviewers read the .po files, and
nothing at runtime notices when the two drift: a .po edited without running
the translations command ships the old strings, silently.
"""

import gettext
from pathlib import Path

import polib
from django.conf import settings
from django.test import SimpleTestCase

WORKSPACE_DIR = Path(settings.BASE_DIR) / "workspace"


def po_files():
    return sorted(WORKSPACE_DIR.glob("*/locale/*/LC_MESSAGES/*.po"))


def mo_catalog(path):
    with path.open("rb") as fh:
        catalog = gettext.GNUTranslations(fh)._catalog
    catalog.pop("", None)
    return catalog


def po_catalog(po):
    """What msgfmt compiles out of a .po: its translated, non-fuzzy entries."""
    catalog = {}
    for entry in po.translated_entries():
        msgid = f"{entry.msgctxt}\x04{entry.msgid}" if entry.msgctxt else entry.msgid
        if entry.msgid_plural:
            for index, msgstr in entry.msgstr_plural.items():
                catalog[(msgid, int(index))] = msgstr
        else:
            catalog[msgid] = entry.msgstr
    return catalog


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
