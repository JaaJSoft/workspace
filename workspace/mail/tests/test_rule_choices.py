"""The mail page hands the browser the server's rule and filing definitions."""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from workspace.mail.services.rules.choices import condition_fields
from workspace.mail.services.rules.schema import (
    ALLOWED_FIELDS,
    SchemaError,
    parse_conditions,
)
from workspace.mail.services.triage import ARCHIVE_REFUSED_FROM, SPAM_REFUSED_FROM

User = get_user_model()


SAMPLE_VALUES = {
    "in_list": ["a"],
    "greater_than": "2026-01-01",
    "less_than": "2026-01-01",
}


class ConditionFieldsTests(TestCase):
    def test_every_field_is_offered_once(self):
        values = [field["value"] for field in condition_fields()]

        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(set(values), ALLOWED_FIELDS)

    def test_every_offered_pair_is_a_condition_the_schema_accepts(self):
        for field in condition_fields():
            for op in field["ops"]:
                leaf = {"field": field["value"], "op": op["value"]}
                if op["takes_value"]:
                    leaf["value"] = SAMPLE_VALUES.get(op["value"], "x")
                with self.subTest(**leaf):
                    try:
                        parse_conditions(leaf)
                    except SchemaError as error:
                        self.fail(error)

    def test_labels_are_translated(self):
        with translation.override("fr"):
            fields = {field["value"]: field for field in condition_fields()}

        self.assertEqual(fields["body"]["label"], "Corps")
        self.assertEqual(fields["from"]["ops"][0]["label"], "contient")


class MailPageDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pagedata", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _embedded(self, html, element_id):
        start = html.index(f'id="{element_id}"')
        start = html.index(">", start) + 1
        return json.loads(html[start : html.index("</script>", start)])

    def test_page_embeds_the_rule_fields_and_the_filing_rules(self):
        html = self.client.get(reverse("mail_ui:index")).content.decode()

        self.assertEqual(
            self._embedded(html, "mail-rule-condition-fields-data"),
            condition_fields(),
        )
        self.assertEqual(
            self._embedded(html, "mail-filing-rules-data"),
            {
                "archive_refused_from": list(ARCHIVE_REFUSED_FROM),
                "spam_refused_from": list(SPAM_REFUSED_FROM),
            },
        )
