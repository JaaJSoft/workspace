import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from workspace.vault.tests.factories import make_account, make_vault, sealed


class CensusCommandTests(TestCase):
    def test_json_output_counts_and_states(self):
        user, _, _ = make_account("owner")
        make_vault(user, encrypted_name=sealed("v", 1))
        out = StringIO()
        call_command("vault_suite_census", "--json", stdout=out)
        rows = {(r["axis"], str(r["id"])): r for r in json.loads(out.getvalue())}
        self.assertEqual(rows[("format", "1")]["count"], 1)
        self.assertEqual(rows[("format", "1")]["state"], "superseded")

    def test_text_output_names_each_axis(self):
        user, _, _ = make_account("owner")
        make_vault(user)
        out = StringIO()
        call_command("vault_suite_census", stdout=out)
        self.assertIn("format 2", out.getvalue())

    def test_an_unparsable_head_is_reported_as_unreadable(self):
        user, _, _ = make_account("owner")
        make_vault(user, encrypted_name="!!!!")
        out = StringIO()
        call_command("vault_suite_census", "--json", stdout=out)
        rows = {(r["axis"], str(r["id"])): r for r in json.loads(out.getvalue())}
        self.assertEqual(rows[("format", "?")]["state"], "unreadable")
