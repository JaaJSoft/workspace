from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.rules.conditions import evaluate_node
from workspace.mail.services.rules.schema import parse_conditions

User = get_user_model()


def _node(field, op, value=None, case_sensitive=False):
    d = {"field": field, "op": op, "case_sensitive": case_sensitive}
    if value is not None:
        d["value"] = value
    return parse_conditions(d)


class _BaseConditionTests(TestCase):
    """Shared setUp for condition tests. No test_* methods here so that
    subclasses don't inherit (and re-run) tests they don't own."""

    def setUp(self):
        self.user = User.objects.create_user(username="cu", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="cu@x.com",
            imap_host="x",
            smtp_host="x",
            username="cu@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="Inbox",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="Quarterly review with Alice",
            from_name="Alice",
            from_email="alice@github.com",
            to_addresses=[{"name": "Bob", "email": "bob@team-x.com"}],
            cc_addresses=[{"name": "Eve", "email": "eve@team-x.com"}],
            body_text="Please find attached the report. Thanks.",
            date=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        )


class TextConditionTests(_BaseConditionTests):
    def test_from_contains_match(self):
        self.assertTrue(
            evaluate_node(_node("from", "contains", "@github.com"), self.msg)
        )

    def test_from_contains_no_match(self):
        self.assertFalse(
            evaluate_node(_node("from", "contains", "@gitlab.com"), self.msg)
        )

    def test_from_matches_name_or_email(self):
        self.assertTrue(evaluate_node(_node("from", "contains", "Alice"), self.msg))

    def test_subject_starts_with(self):
        self.assertTrue(
            evaluate_node(_node("subject", "starts_with", "Quarter"), self.msg)
        )
        self.assertFalse(
            evaluate_node(_node("subject", "starts_with", "review"), self.msg)
        )

    def test_subject_ends_with(self):
        self.assertTrue(evaluate_node(_node("subject", "ends_with", "Alice"), self.msg))

    def test_subject_equals_case_insensitive(self):
        self.assertTrue(
            evaluate_node(
                _node("subject", "equals", "QUARTERLY REVIEW WITH ALICE"),
                self.msg,
            )
        )

    def test_subject_equals_case_sensitive_false(self):
        self.assertFalse(
            evaluate_node(
                _node(
                    "subject",
                    "equals",
                    "QUARTERLY REVIEW WITH ALICE",
                    case_sensitive=True,
                ),
                self.msg,
            )
        )

    def test_recipient_matches_to_or_cc(self):
        self.assertTrue(evaluate_node(_node("recipient", "contains", "bob@"), self.msg))
        self.assertTrue(evaluate_node(_node("recipient", "contains", "eve@"), self.msg))

    def test_body_contains(self):
        self.assertTrue(evaluate_node(_node("body", "contains", "report"), self.msg))

    def test_folder_equals(self):
        self.assertTrue(evaluate_node(_node("folder", "equals", "Inbox"), self.msg))

    def test_in_list_match_any(self):
        self.assertTrue(
            evaluate_node(
                _node("from", "in_list", ["noreply@x.com", "alice@github.com"]),
                self.msg,
            )
        )
        self.assertFalse(
            evaluate_node(
                _node("from", "in_list", ["noreply@x.com"]),
                self.msg,
            )
        )


class BooleanDateConditionTests(_BaseConditionTests):
    def test_is_starred_true(self):
        self.msg.is_starred = True
        self.msg.save(update_fields=["is_starred"])
        self.assertTrue(evaluate_node(_node("is_starred", "is_true"), self.msg))
        self.assertFalse(evaluate_node(_node("is_starred", "is_false"), self.msg))

    def test_has_attachments_false(self):
        self.assertTrue(evaluate_node(_node("has_attachments", "is_false"), self.msg))

    def test_date_greater_than(self):
        self.assertTrue(
            evaluate_node(
                _node("date", "greater_than", "2026-04-01T00:00:00+00:00"),
                self.msg,
            )
        )
        self.assertFalse(
            evaluate_node(
                _node("date", "greater_than", "2026-06-01T00:00:00+00:00"),
                self.msg,
            )
        )

    def test_date_less_than(self):
        self.assertTrue(
            evaluate_node(
                _node("date", "less_than", "2026-06-01T00:00:00+00:00"),
                self.msg,
            )
        )

    def test_date_invalid_value_rejected_at_parse(self):
        # Schema validation now rejects invalid ISO 8601 strings at parse
        # time, so a malformed date never reaches the evaluator.
        from workspace.mail.services.rules.schema import SchemaError

        with self.assertRaises(SchemaError):
            _node("date", "greater_than", "not-a-date")


class RegexConditionTests(_BaseConditionTests):
    def test_regex_match(self):
        self.assertTrue(
            evaluate_node(
                _node("from", "matches_regex", r"^alice@.*\.com$"),
                self.msg,
            )
        )

    def test_regex_no_match(self):
        self.assertFalse(
            evaluate_node(
                _node("from", "matches_regex", r"^bob@"),
                self.msg,
            )
        )


class GroupConditionTests(_BaseConditionTests):
    def test_all_group_true(self):
        node = parse_conditions(
            {
                "type": "all",
                "conditions": [
                    {"field": "from", "op": "contains", "value": "@github.com"},
                    {"field": "subject", "op": "contains", "value": "review"},
                ],
            }
        )
        self.assertTrue(evaluate_node(node, self.msg))

    def test_all_group_false_when_one_fails(self):
        node = parse_conditions(
            {
                "type": "all",
                "conditions": [
                    {"field": "from", "op": "contains", "value": "@github.com"},
                    {"field": "subject", "op": "contains", "value": "XYZ"},
                ],
            }
        )
        self.assertFalse(evaluate_node(node, self.msg))

    def test_any_group_true_when_one_matches(self):
        node = parse_conditions(
            {
                "type": "any",
                "conditions": [
                    {"field": "from", "op": "contains", "value": "nobody"},
                    {"field": "subject", "op": "contains", "value": "review"},
                ],
            }
        )
        self.assertTrue(evaluate_node(node, self.msg))

    def test_nested_all_inside_any(self):
        node = parse_conditions(
            {
                "type": "any",
                "conditions": [
                    {
                        "type": "all",
                        "conditions": [
                            {"field": "from", "op": "contains", "value": "@github.com"},
                            {"field": "subject", "op": "contains", "value": "review"},
                        ],
                    },
                    {"field": "subject", "op": "contains", "value": "urgent"},
                ],
            }
        )
        self.assertTrue(evaluate_node(node, self.msg))

    def test_empty_all_is_true(self):
        node = parse_conditions({"type": "all", "conditions": []})
        self.assertTrue(evaluate_node(node, self.msg))

    def test_empty_any_is_false(self):
        node = parse_conditions({"type": "any", "conditions": []})
        self.assertFalse(evaluate_node(node, self.msg))
