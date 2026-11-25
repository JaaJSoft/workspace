from django.test import SimpleTestCase


class StatusLineTranslationTests(SimpleTestCase):
    """The counts under the vault listing come out of the French script catalog."""

    def test_folder_and_entry_counts_are_translated(self):
        response = self.client.get("/i18n/fr")
        self.assertContains(response, '"%(count)s folder"')
        self.assertContains(response, '"%(count)s entry"')
