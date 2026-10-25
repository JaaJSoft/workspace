from pathlib import Path

from django.core.management import CommandError, call_command
from django.core.management.commands.makemessages import Command as MakeMessages
from django.test import SimpleTestCase

from workspace.core.management.commands.translations import (
    SHARED_MODULE,
    _MakeMessages,
    shared_catalogs,
    tidy_new_header,
    translated_languages,
    workspace_modules,
)

XGETTEXT_TEMPLATE = """# SOME DESCRIPTIVE TITLE.
# Copyright (C) YEAR THE PACKAGE'S COPYRIGHT HOLDER
# This file is distributed under the same license as the PACKAGE package.
# FIRST AUTHOR <EMAIL@ADDRESS>, YEAR.
#
#, fuzzy
msgid ""
msgstr ""
"Project-Id-Version: PACKAGE VERSION\\n"
"Report-Msgid-Bugs-To: \\n"
"POT-Creation-Date: 2026-10-25 16:06+0000\\n"
"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\\n"
"Last-Translator: FULL NAME <EMAIL@ADDRESS>\\n"
"Language-Team: LANGUAGE <LL@li.org>\\n"
"Language: \\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=2; plural=(n > 1);\\n"

#: templates/page.html
msgid "Settings"
msgstr ""
"""


class TidyNewHeaderTests(SimpleTestCase):
    def test_fills_in_the_template_header(self):
        tidy = tidy_new_header(XGETTEXT_TEMPLATE, "fr")
        self.assertTrue(tidy.startswith('msgid ""\nmsgstr ""\n'))
        self.assertIn('"Project-Id-Version: workspace\\n"', tidy)
        self.assertIn('"Language: fr\\n"', tidy)
        self.assertIn('"Plural-Forms: nplurals=2; plural=(n > 1);\\n"', tidy)
        for placeholder in (
            "fuzzy",
            "FIRST AUTHOR",
            "YEAR",
            "LANGUAGE <",
            "Report-Msgid",
        ):
            self.assertNotIn(placeholder, tidy)

    def test_leaves_the_messages_alone(self):
        tidy = tidy_new_header(XGETTEXT_TEMPLATE, "fr")
        self.assertTrue(
            tidy.endswith('#: templates/page.html\nmsgid "Settings"\nmsgstr ""\n')
        )


class TranslationsCommandTests(SimpleTestCase):
    def test_modules_are_the_top_level_apps(self):
        modules = workspace_modules()
        self.assertIn("users", modules)
        self.assertIn("common", modules)
        self.assertNotIn("ui", modules)

    def test_source_language_gets_no_catalog(self):
        self.assertEqual(translated_languages(), ["fr"])

    def test_unknown_module_is_refused(self):
        with self.assertRaisesMessage(CommandError, "Unknown module(s): nope"):
            call_command("translations", "nope")


class SharedCatalogsTests(SimpleTestCase):
    def test_a_module_excludes_the_shared_catalogs_of_its_domain(self):
        catalogs = shared_catalogs(workspace_modules()["dashboard"], "django")
        self.assertIn(
            workspace_modules()[SHARED_MODULE] / "locale/fr/LC_MESSAGES/django.po",
            catalogs,
        )
        self.assertTrue(all(catalog.name == "django.po" for catalog in catalogs))

    def test_the_shared_module_excludes_nothing(self):
        self.assertEqual(
            shared_catalogs(workspace_modules()[SHARED_MODULE], "django"), []
        )

    def test_extraction_passes_the_catalogs_to_xgettext(self):
        command = _MakeMessages([Path("/x/common/locale/fr/LC_MESSAGES/django.po")])
        self.assertIn(
            "--exclude-file=/x/common/locale/fr/LC_MESSAGES/django.po",
            command.xgettext_options,
        )
        # The class attribute stays untouched for plain makemessages runs.
        self.assertNotIn("--exclude-file", " ".join(MakeMessages.xgettext_options))
