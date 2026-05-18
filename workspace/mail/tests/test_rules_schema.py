from django.test import SimpleTestCase

from workspace.mail.services.rules.schema import (
    GroupCondition,
    LeafCondition,
    parse_conditions,
    SchemaError,
)


class LeafConditionTests(SimpleTestCase):
    def test_parse_minimal_leaf(self):
        node = parse_conditions({'field': 'from', 'op': 'contains', 'value': '@x.com'})
        self.assertIsInstance(node, LeafCondition)
        self.assertEqual(node.field, 'from')
        self.assertEqual(node.op, 'contains')
        self.assertEqual(node.value, '@x.com')
        self.assertFalse(node.case_sensitive)

    def test_unknown_field_rejected(self):
        with self.assertRaises(SchemaError):
            parse_conditions({'field': 'bogus', 'op': 'contains', 'value': 'x'})

    def test_unknown_op_rejected(self):
        with self.assertRaises(SchemaError):
            parse_conditions({'field': 'from', 'op': 'bogus', 'value': 'x'})

    def test_text_op_needs_value(self):
        with self.assertRaises(SchemaError):
            parse_conditions({'field': 'subject', 'op': 'contains'})

    def test_bool_op_no_value_required(self):
        node = parse_conditions({'field': 'is_starred', 'op': 'is_true'})
        self.assertIsInstance(node, LeafCondition)

    def test_in_list_value_must_be_list(self):
        with self.assertRaises(SchemaError):
            parse_conditions({'field': 'from', 'op': 'in_list', 'value': 'foo'})


class GroupConditionTests(SimpleTestCase):
    def test_parse_empty_group(self):
        node = parse_conditions({'type': 'all', 'conditions': []})
        self.assertIsInstance(node, GroupCondition)
        self.assertEqual(node.type, 'all')
        self.assertEqual(node.conditions, [])

    def test_parse_nested(self):
        node = parse_conditions({
            'type': 'all',
            'conditions': [
                {'field': 'from', 'op': 'contains', 'value': 'x'},
                {'type': 'any', 'conditions': [
                    {'field': 'subject', 'op': 'contains', 'value': 'a'},
                    {'field': 'subject', 'op': 'contains', 'value': 'b'},
                ]},
            ],
        })
        self.assertEqual(len(node.conditions), 2)
        self.assertIsInstance(node.conditions[1], GroupCondition)
        self.assertEqual(node.conditions[1].type, 'any')

    def test_unknown_group_type_rejected(self):
        with self.assertRaises(SchemaError):
            parse_conditions({'type': 'xor', 'conditions': []})
