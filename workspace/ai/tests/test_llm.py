import json

from django.test import TestCase

from workspace.ai.services.llm import extract_text_tool_calls


class ExtractTextToolCallsTests(TestCase):

    def test_returns_none_when_no_json(self):
        calls, remaining = extract_text_tool_calls('just a regular reply')
        self.assertIsNone(calls)
        self.assertEqual(remaining, 'just a regular reply')

    def test_returns_none_when_json_is_not_a_tool_call(self):
        content = 'reply with {"unrelated": "data"}'
        calls, remaining = extract_text_tool_calls(content)
        self.assertIsNone(calls)
        self.assertEqual(remaining, content)

    def test_shorthand_tool_form(self):
        content = '{"tool": "search_files", "query": "report", "limit": 5}'
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args_json = calls[0]
        self.assertEqual(name, 'search_files')
        # Remaining keys are re-emitted as the arguments JSON.
        self.assertEqual(json.loads(args_json), {'query': 'report', 'limit': 5})
        self.assertEqual(remaining, '')

    def test_openai_like_form_with_dict_arguments(self):
        content = '{"name": "create_event", "arguments": {"title": "Lunch", "duration": 30}}'
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args_json = calls[0]
        self.assertEqual(name, 'create_event')
        self.assertEqual(json.loads(args_json), {'title': 'Lunch', 'duration': 30})
        self.assertEqual(remaining, '')

    def test_openai_like_form_with_string_arguments(self):
        content = '{"name": "echo", "arguments": "raw string"}'
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args_json = calls[0]
        self.assertEqual(name, 'echo')
        # String arguments must be passed through untouched (not double-encoded).
        self.assertEqual(args_json, 'raw string')

    def test_text_around_tool_call_is_returned_in_remaining(self):
        content = 'Here you go: {"tool": "noop"} - that is all'
        calls, remaining = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'noop')
        self.assertIn('Here you go:', remaining)
        self.assertIn('that is all', remaining)
        self.assertNotIn('{"tool"', remaining)

    def test_multiple_tool_calls_in_one_message(self):
        content = '{"tool": "a", "x": 1} and also {"name": "b", "arguments": {"y": 2}}'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], 'a')
        self.assertEqual(json.loads(calls[0][1]), {'x': 1})
        self.assertEqual(calls[1][0], 'b')
        self.assertEqual(json.loads(calls[1][1]), {'y': 2})

    def test_invalid_json_is_ignored(self):
        content = '{not really json} but {"tool": "ok"} should still work'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'ok')

    def test_non_dict_json_is_ignored(self):
        content = '{"tool": "ok"}'
        # Plain list/string JSON would also be matched by the regex; ensure only dicts are taken.
        # Adding a list before the dict should not break parsing of the dict.
        calls, _ = extract_text_tool_calls('[1, 2, 3] ' + content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'ok')

    def test_raw_tool_call_tags_are_stripped(self):
        # <tool_call> wrapper tags some models emit must not block parsing.
        content = '<tool_call>{"tool": "search", "q": "x"}</tool_call>'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'search')

    def test_shorthand_with_no_extra_keys(self):
        # When the shorthand form has only the "tool" key, the arguments JSON is an empty object.
        content = '{"tool": "ping"}'
        calls, _ = extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'ping')
        self.assertEqual(json.loads(calls[0][1]), {})
