"""A tool call that fails: what the tool raises, and what the model reads.

The tools are a registry of this file's own and the model is scripted, so
each test reads the result the run handed back to the model.
"""

from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_core import ValidationError

from workspace.ai.prompts.base import prompt
from workspace.ai.services.bot_runs import run_bot
from workspace.ai.tool_registry import (
    RETRYABLE_NOTE,
    ToolDeps,
    ToolError,
    ToolProvider,
    ToolRegistry,
    tool,
)

from .agents import call, reply, run_tool, scripted


class _Params(BaseModel):
    count: int


class _TextParams(BaseModel):
    text: str


class _Provider(ToolProvider):
    @tool()
    def fail_final(self, ctx: RunContext[ToolDeps], args: _Params) -> str:
        """Fails for good."""
        raise ToolError("no such thing")

    @tool()
    def fail_transient(self, ctx: RunContext[ToolDeps]) -> str:
        """Fails for now."""
        raise ToolError("service down", retryable=True)

    @tool()
    def fail_in_french(self, ctx: RunContext[ToolDeps]) -> str:
        """Fails with an accent."""
        raise ToolError("événement introuvable")

    @tool()
    def crash(self, ctx: RunContext[ToolDeps]) -> str:
        """Has a bug."""
        raise RuntimeError("bug")

    @tool()
    def echo(self, ctx: RunContext[ToolDeps], args: _TextParams) -> str:
        """Returns the text it is given."""
        return args.text


class _Bot:
    """Stands for a bot profile: the model it runs on."""

    def get_model(self):
        return None


class _RegistryCase(SimpleTestCase):
    def setUp(self):
        registry = ToolRegistry()
        registry.register_provider(_Provider())
        patcher = patch("workspace.ai.services.bot_runs.tool_registry", registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = registry


class ToolErrorTests(SimpleTestCase):
    def test_a_tool_error_is_a_failure_the_library_reports_to_the_model(self):
        error = ToolError("no such thing")

        self.assertIsInstance(error, ToolFailed)
        self.assertEqual(str(error), "no such thing")
        self.assertEqual((error.reason, error.retryable), ("no such thing", False))

    def test_a_retryable_failure_says_so(self):
        error = ToolError("service down", retryable=True)

        self.assertEqual((error.reason, error.retryable), ("service down", True))
        # The note is what tells the model: it never reads the flag.
        self.assertEqual(str(error), f"service down {RETRYABLE_NOTE}")
        self.assertEqual(error.message, str(error))


class CalledToolTests(_RegistryCase):
    """A registered tool called outside of a run: a failure raises."""

    def test_a_raised_tool_error_comes_out_as_it_was_raised(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("fail_final", '{"count": 1}', registry=self.registry)

        self.assertEqual(caught.exception.reason, "no such thing")
        self.assertFalse(caught.exception.retryable)

    def test_a_retryable_failure_says_so(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("fail_transient", registry=self.registry)

        self.assertEqual(caught.exception.reason, "service down")
        self.assertTrue(caught.exception.retryable)

    def test_arguments_that_do_not_fit_the_schema_never_reach_the_tool(self):
        cases = {
            "invalid JSON": "{not json",
            "invalid arguments": '{"count": "many"}',
            "missing argument": "{}",
        }
        for label, arguments in cases.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                run_tool("fail_final", arguments, registry=self.registry)

    def test_a_tool_nobody_registered_is_not_on_offer(self):
        self.assertNotIn("teleport_user", self.registry.names())
        self.assertNotIn(
            "teleport_user", [registered.name for registered in self.registry.get_all()]
        )

    def test_any_other_exception_is_a_bug_and_propagates(self):
        with self.assertRaises(RuntimeError):
            run_tool("crash", registry=self.registry)


class FailureReadByTheModelTests(_RegistryCase):
    """Inside a run, the library turns a failure into the result of the call."""

    def run_calls(self, *calls):
        """Run one round of *calls*: the parts sent back, and the stored results."""
        with scripted(reply("", *calls), reply("done")) as script:
            run = run_bot(_Bot(), None, None, None, prompt("be helpful", "go"))
        parts = [
            part
            for message in script.sent
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        return parts, run.tool_data[0]["results"]

    def test_a_raised_tool_error_is_read_as_an_error_and_recorded_as_a_failure(self):
        (part,), (stored,) = self.run_calls(call("fail_final", {"count": 1}, "c1"))

        self.assertEqual(part.outcome, "failed")
        self.assertEqual(part.model_response_object(), {"error": "no such thing"})
        self.assertEqual(
            stored,
            {"tool_call_id": "c1", "content": "no such thing", "outcome": "failed"},
        )

    def test_a_retryable_failure_says_so(self):
        (part,), (stored,) = self.run_calls(call("fail_transient", {}, "c1"))

        self.assertEqual(
            part.model_response_object(), {"error": f"service down {RETRYABLE_NOTE}"}
        )
        self.assertEqual(stored["content"], f"service down {RETRYABLE_NOTE}")
        self.assertEqual(stored["outcome"], "failed")

    def test_the_library_reports_its_own_refusals_as_failures_too(self):
        cases = {
            "invalid JSON": call("fail_final", "{not json", "c1"),
            "unknown tool": call("teleport_user", {}, "c1"),
            "invalid arguments": call("fail_final", {"count": "many"}, "c1"),
        }
        for label, wrong in cases.items():
            with self.subTest(label):
                (part,), (stored,) = self.run_calls(wrong)

                self.assertIn("error", part.model_response_object())
                self.assertEqual(stored["outcome"], "failed")
                self.assertNotIn(RETRYABLE_NOTE, stored["content"])

        _, (unknown,) = self.run_calls(call("teleport_user", {}, "c1"))
        self.assertIn("teleport_user", unknown["content"])

    def test_any_other_exception_is_a_bug_and_fails_the_run(self):
        with self.assertRaises(RuntimeError):
            self.run_calls(call("crash", {}, "c1"))

    def test_keeps_non_ascii_reasons_readable_to_the_model(self):
        (part,), (stored,) = self.run_calls(call("fail_in_french", {}, "c1"))

        self.assertIn("é", part.model_response_str())
        self.assertEqual(stored["content"], "événement introuvable")

    def test_the_failure_reaches_the_model_as_text(self):
        (part,), _ = self.run_calls(call("fail_final", {"count": 1}, "c1"))

        self.assertEqual(part.model_response_str(), '{"error":"no such thing"}')

    def test_what_a_tool_returns_is_a_result_whatever_it_looks_like(self):
        # read_webpage hands back JSON APIs verbatim, and their own error
        # bodies are what the page said, not a failure of the call.
        texts = [
            "Error: query is required",
            "plain text",
            '{"error": "rate limited"}',
            '{"error": {"code": 500, "message": "boom"}}',
            '{"error": {"retryable": false, "reason": "x"}}',
            "[]",
            "{not json",
        ]

        parts, stored = self.run_calls(
            *[call("echo", {"text": text}, f"c{i}") for i, text in enumerate(texts)]
        )

        self.assertEqual([part.outcome for part in parts], ["success"] * len(texts))
        self.assertEqual([part.model_response_str() for part in parts], texts)
        self.assertEqual(
            stored,
            [
                {"tool_call_id": f"c{i}", "content": text}
                for i, text in enumerate(texts)
            ],
        )
