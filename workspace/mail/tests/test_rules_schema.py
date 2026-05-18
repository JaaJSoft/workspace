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


from workspace.mail.services.rules.schema import (
    MAX_DEPTH, MAX_LEAVES, validate_tree_limits,
)


class TreeLimitsTests(SimpleTestCase):
    def _leaf(self, value='x'):
        return {'field': 'from', 'op': 'contains', 'value': value}

    def test_depth_one_ok(self):
        node = parse_conditions(self._leaf())
        validate_tree_limits(node)  # no raise

    def test_depth_exceeds_limit(self):
        # Build a tree of depth MAX_DEPTH + 1
        node_dict = self._leaf()
        for _ in range(MAX_DEPTH + 1):
            node_dict = {'type': 'all', 'conditions': [node_dict]}
        node = parse_conditions(node_dict)
        with self.assertRaises(SchemaError):
            validate_tree_limits(node)

    def test_too_many_leaves(self):
        try:
            node = parse_conditions({
                'type': 'all',
                'conditions': [self._leaf(str(i)) for i in range(MAX_LEAVES + 1)],
            })
        except SchemaError:
            return  # pydantic rejected at parse time - valid
        with self.assertRaises(SchemaError):
            validate_tree_limits(node)


from workspace.mail.services.rules.schema import (
    AddLabelAction,
    DeleteAction,
    MarkReadAction,
    parse_actions,
)


class ActionSchemaTests(SimpleTestCase):
    def test_parse_mark_read(self):
        actions = parse_actions([{'type': 'mark_read'}])
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], MarkReadAction)

    def test_parse_add_label_with_uuid(self):
        uid = '01934e2e-1111-7777-8888-abcdef000001'
        actions = parse_actions([{'type': 'add_label', 'label_id': uid}])
        self.assertIsInstance(actions[0], AddLabelAction)
        self.assertEqual(str(actions[0].label_id), uid)

    def test_add_label_missing_uuid_rejected(self):
        with self.assertRaises(SchemaError):
            parse_actions([{'type': 'add_label'}])

    def test_move_to_folder_requires_uuid(self):
        with self.assertRaises(SchemaError):
            parse_actions([{'type': 'move_to_folder'}])

    def test_unknown_action_type_rejected(self):
        with self.assertRaises(SchemaError):
            parse_actions([{'type': 'forward_to', 'email': 'x@y.z'}])

    def test_empty_list_ok(self):
        self.assertEqual(parse_actions([]), [])

    def test_delete_action(self):
        actions = parse_actions([{'type': 'delete'}])
        self.assertIsInstance(actions[0], DeleteAction)
