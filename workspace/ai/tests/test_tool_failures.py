import json

from django.test import SimpleTestCase
from pydantic import BaseModel

from workspace.ai.harness.model import ToolCall
from workspace.ai.services.llm import build_tool_content
from workspace.ai.tool_registry import (
    ToolError,
    ToolProvider,
    ToolRegistry,
    parse_tool_failure,
    tool,
    tool_failure,
)


class _Params(BaseModel):
    count: int


class _Provider(ToolProvider):
    @tool(params=_Params)
    def fail_final(self, args, user, bot, conversation_id, context):
        """Fails for good."""
        raise ToolError("no such thing")

    @tool()
    def fail_transient(self, args, user, bot, conversation_id, context):
        """Fails for now."""
        raise ToolError("service down", retryable=True)

    @tool()
    def crash(self, args, user, bot, conversation_id, context):
        """Has a bug."""
        raise RuntimeError("bug")


class ExecuteFailureEnvelopeTests(SimpleTestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register_provider(_Provider())

    def _execute(self, name, arguments="{}"):
        call = ToolCall(id="c1", name=name, arguments=arguments)
        return self.registry.execute(call, user=None, bot=None)

    def test_a_raised_tool_error_comes_back_as_a_final_envelope(self):
        result = self._execute("fail_final", '{"count": 1}')

        self.assertEqual(
            json.loads(result),
            {"error": {"retryable": False, "reason": "no such thing"}},
        )

    def test_a_retryable_failure_says_so(self):
        failure = parse_tool_failure(self._execute("fail_transient"))

        self.assertEqual(failure, {"retryable": True, "reason": "service down"})

    def test_the_registry_reports_its_own_refusals_in_the_same_shape(self):
        cases = {
            "invalid JSON": ("fail_final", "{not json"),
            "unknown tool": ("teleport_user", "{}"),
            "invalid arguments": ("fail_final", '{"count": "many"}'),
        }
        for label, (name, arguments) in cases.items():
            with self.subTest(label):
                failure = parse_tool_failure(self._execute(name, arguments))
                self.assertIsNotNone(failure)
                self.assertFalse(failure["retryable"])

        unknown = parse_tool_failure(self._execute("teleport_user"))
        self.assertIn("teleport_user", unknown["reason"])

    def test_any_other_exception_is_a_bug_and_propagates(self):
        with self.assertRaises(RuntimeError):
            self._execute("crash")


class ParseToolFailureTests(SimpleTestCase):
    def test_round_trips_the_envelope(self):
        self.assertEqual(
            parse_tool_failure(tool_failure("gone", retryable=True)),
            {"retryable": True, "reason": "gone"},
        )

    def test_keeps_non_ascii_reasons_readable_to_the_model(self):
        self.assertIn("é", tool_failure("événement introuvable"))

    def test_anything_but_the_exact_shape_is_a_result(self):
        # read_webpage hands back JSON APIs verbatim, and their own error
        # bodies are what the page said, not a failure of the call.
        for result in (
            "Error: query is required",
            "plain text",
            '{"error": "rate limited"}',
            '{"error": {"code": 500, "message": "boom"}}',
            '{"error": {"retryable": "yes", "reason": "x"}}',
            '{"error": {"retryable": false, "reason": "x"}, "data": []}',
            "[]",
            "{not json",
            None,
            ["a list"],
        ):
            with self.subTest(result=result):
                self.assertIsNone(parse_tool_failure(result))

    def test_the_envelope_reaches_the_model_as_text(self):
        envelope = tool_failure("no such thing")

        self.assertEqual(build_tool_content(envelope), envelope)
