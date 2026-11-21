"""The behaviors of the tool loop, held against the Pydantic AI runtime.

Each class here has a twin in ``test_harness_runner.py``: what stops a run
and what it leaves behind must not depend on which runtime answered.
"""

import itertools
import json

from django.test import TestCase
from pydantic_ai.messages import ModelResponse as AgentResponse
from pydantic_ai.messages import TextPart, ToolCallPart

from workspace.ai.harness.observers import Observer
from workspace.ai.harness.runner import StopReason
from workspace.ai.tool_registry import parse_tool_failure

from .harness import ScriptedModel, StubToolset, call, reply, tool_reply
from .pydantic_runtime import build_runner, tool_results


def _run(model, toolset=None, **limits):
    toolset = toolset or StubToolset(
        handler=lambda tc, ctx: "ok", definitions=("search",)
    )
    runner = build_runner(model, toolset, **limits)
    return runner.run([{"role": "user", "content": "go"}])


def _sent(model, index=-1):
    """The conversation the model was sent on one of its requests."""
    return model.requests[index][0]


class RoundCapTests(TestCase):
    def test_loop_stops_at_configured_round_cap(self):
        model = ScriptedModel([tool_reply(call("call_1"))], repeat=True)
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset, max_rounds=2, max_identical_calls=5)

        # 1 initial call + one re-call per allowed round + the tool-less call
        # that turns the exhausted run into an answer
        self.assertEqual(len(model.requests), 4)
        self.assertEqual(len(toolset.executed), 2)
        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertIsNone(model.last_tools)
        self.assertEqual(len(run.rounds), 4)
        self.assertTrue(run.rounds[-2]["round_cap_reached"])
        self.assertNotIn("tool_executions", run.rounds[-1])

    def test_the_forced_answer_is_asked_without_the_call_that_never_ran(self):
        model = ScriptedModel(
            [tool_reply(call("c1")), tool_reply(call("c2", arguments='{"q": 2}'))],
            repeat=True,
        )

        _run(model, max_rounds=1)

        # A call left without its result is a conversation no backend accepts.
        asked = [
            part.tool_call_id
            for message in _sent(model)
            if isinstance(message, AgentResponse)
            for part in message.parts
            if isinstance(part, ToolCallPart)
        ]
        self.assertEqual(asked, ["c1"])
        self.assertEqual(tool_results(_sent(model)), [("c1", "ok")])

    def test_no_extra_call_when_cap_lands_on_a_text_answer(self):
        looping = tool_reply(call("call_1"))
        model = ScriptedModel([looping, looping, reply("done")])

        run = _run(model, max_rounds=2)

        self.assertEqual(len(model.requests), 3)
        self.assertEqual(run.response.content, "done")
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertNotIn("round_cap_reached", run.rounds[-1])

    def test_tools_are_offered_on_every_round_but_the_forced_answer(self):
        model = ScriptedModel([tool_reply(call("call_1"))], repeat=True)

        _run(model, max_rounds=1)

        self.assertEqual(
            [tools for _, tools in model.requests], [["search"], ["search"], None]
        )


class StopAfterRoundTests(TestCase):
    def test_stop_after_round_halts_loop(self):
        def halt(tool_call, context):
            context["stop_after_round"] = True
            return "ok"

        model = ScriptedModel(
            [tool_reply(call("call_1", name="halt_tool"))], repeat=True
        )

        run = _run(model, StubToolset(handler=halt, definitions=("halt_tool",)))

        self.assertEqual(len(model.requests), 1)
        self.assertIs(run.stop, StopReason.AWAITING_USER)
        self.assertTrue(run.context["stop_after_round"])
        self.assertTrue(run.rounds[-1]["terminated_by_tool"])

    def test_a_halted_run_asked_again_answers_from_the_round_it_made(self):
        def halt(tool_call, context):
            context["stop_after_round"] = True
            return "asked"

        model = ScriptedModel(
            [tool_reply(call("call_1", name="halt_tool")), reply("Which one?")]
        )
        messages = [{"role": "user", "content": "go"}]
        runner = build_runner(
            model, StubToolset(handler=halt, definitions=("halt_tool",))
        )
        run = runner.run(messages)

        retried = runner.retry_final(messages, run)

        self.assertEqual(retried.response.content, "Which one?")
        self.assertIs(retried.stop, StopReason.AWAITING_USER)
        self.assertEqual(tool_results(_sent(model)), [("call_1", "asked")])


class AnsweredTests(TestCase):
    def test_a_reply_without_tools_ends_the_run_at_once(self):
        model = ScriptedModel([reply("hello")])

        run = _run(model)

        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "hello")
        self.assertEqual(len(run.rounds), 1)
        self.assertEqual(run.rounds[0]["response"]["content"], "hello")
        self.assertIsNone(run.tool_data)

    def test_an_empty_reply_ends_the_run_for_the_caller_to_retry(self):
        # The library would ask the model again on its own; whether an empty
        # reply is retried is the task's call, which knows what the tools
        # left behind.
        model = ScriptedModel([reply("")])

        run = _run(model)

        self.assertEqual(len(model.requests), 1)
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "")

    def test_the_conversation_carries_each_round(self):
        model = ScriptedModel(
            [
                tool_reply(
                    call("c1", arguments='{"q": 1}'), content="looking", thinking="hmm"
                ),
                reply("done"),
            ]
        )

        run = _run(model)

        turn = _sent(model)[1]
        self.assertIsInstance(turn, AgentResponse)
        self.assertEqual(
            [part.content for part in turn.parts if isinstance(part, TextPart)],
            ["looking"],
        )
        self.assertEqual(tool_results(_sent(model)), [("c1", "ok")])
        # What the round said, without its reasoning: that is kept apart.
        self.assertEqual(run.tool_data[0]["assistant_content"], "looking")
        self.assertEqual(run.tool_data[0]["thinking"], "hmm")
        self.assertEqual(
            run.tool_data[0]["tool_calls"],
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q": 1}'},
                }
            ],
        )
        self.assertEqual(
            run.tool_data[0]["results"], [{"tool_call_id": "c1", "content": "ok"}]
        )
        self.assertEqual(run.rounds[0]["tool_executions"][0]["name"], "search")

    def test_retry_final_asks_again_without_tools_and_runs_nothing(self):
        model = ScriptedModel([tool_reply(call("c1")), reply(""), reply("done")])
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))
        messages = [{"role": "user", "content": "go"}]
        runner = build_runner(model, toolset)
        run = runner.run(messages)

        retried = runner.retry_final(messages, run)

        self.assertEqual(retried.response.content, "done")
        self.assertIsNone(model.last_tools)
        self.assertEqual(len(toolset.executed), 1)
        self.assertEqual(len(retried.rounds), 3)
        self.assertIs(retried.rounds, run.rounds)
        # Asked on what the run gathered, not on the bare question.
        self.assertEqual(tool_results(_sent(model)), [("c1", "ok")])


class UsageTests(TestCase):
    def test_usage_sums_every_reply_of_the_run(self):
        model = ScriptedModel(
            [
                tool_reply(call("c1"), prompt_tokens=100, completion_tokens=10),
                tool_reply(
                    call("c2", arguments='{"q": 2}'),
                    prompt_tokens=150,
                    completion_tokens=20,
                ),
                reply("done", prompt_tokens=200, completion_tokens=30),
            ]
        )

        run = _run(model)

        self.assertEqual(run.usage.prompt_tokens, 450)
        self.assertEqual(run.usage.completion_tokens, 60)
        self.assertGreater(run.usage.seconds, 0)
        self.assertEqual(run.response.completion_tokens, 30)

    def test_the_forced_answer_after_the_round_cap_counts(self):
        looping = tool_reply(call("c1"), prompt_tokens=10, completion_tokens=1)
        model = ScriptedModel([looping], repeat=True)

        run = _run(model, max_rounds=1)

        # initial call + one allowed round + the tool-less call after the cap
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(run.usage.prompt_tokens, 30)

    def test_retry_final_adds_the_extra_call(self):
        model = ScriptedModel(
            [
                tool_reply(call("c1"), prompt_tokens=10, completion_tokens=1),
                reply("", prompt_tokens=20, completion_tokens=0),
                reply("done", prompt_tokens=30, completion_tokens=5),
            ]
        )
        messages = [{"role": "user", "content": "go"}]
        runner = build_runner(
            model, StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))
        )
        run = runner.run(messages)

        retried = runner.retry_final(messages, run)

        self.assertEqual(retried.usage.prompt_tokens, 60)
        self.assertEqual(retried.usage.completion_tokens, 6)


class ThinkingPersistenceTests(TestCase):
    def test_round_thinking_is_stored_in_tool_data(self):
        model = ScriptedModel(
            [
                tool_reply(call("call_1"), thinking="I should search first"),
                reply("Done", thinking="now I can answer"),
            ]
        )

        run = _run(model)

        self.assertEqual(len(run.tool_data), 1)
        self.assertEqual(run.tool_data[0]["thinking"], "I should search first")
        # Final-response thinking is NOT in tool_data (post_bot_message appends it)
        self.assertEqual(run.response.thinking, "now I can answer")

    def test_reasoning_written_between_inline_tags_is_cut_from_the_answer(self):
        model = ScriptedModel([reply("<thought>let me see</thought>The answer")])

        run = _run(model)

        self.assertEqual(run.response.content, "The answer")
        self.assertEqual(run.response.thinking, "let me see")


class CancellationTests(TestCase):
    def test_a_cancellation_read_after_the_round_buys_no_more_model_call(self):
        cancelled = []

        def cancel(tool_call, context):
            cancelled.append(True)
            return "ok"

        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)

        run = _run(
            model,
            StubToolset(handler=cancel, definitions=("search",)),
            is_cancelled=lambda: bool(cancelled),
        )

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(len(model.requests), 1)
        self.assertTrue(run.rounds[-1]["cancelled"])

    def test_a_run_cancelled_before_any_tool_runs_none(self):
        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset, is_cancelled=lambda: True)

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(toolset.executed, [])
        self.assertEqual(run.tool_data[0]["results"], [])
        self.assertEqual(len(model.requests), 1)


class RepeatedToolCallTests(TestCase):
    def test_identical_call_runs_at_most_the_configured_number_of_times(self):
        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset, max_identical_calls=3)

        self.assertEqual(len(toolset.executed), 3)
        self.assertIs(run.stop, StopReason.REPEAT_LOOP)

    def test_refused_call_reports_why_to_the_model(self):
        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)

        run = _run(model)

        refused = run.rounds[3]["tool_executions"][0]["result"]
        failure = parse_tool_failure(refused)
        self.assertIn("not executed", failure["reason"])
        self.assertIn("search", failure["reason"])
        self.assertEqual(tool_results(_sent(model))[-1][1], refused)

    def test_repeat_loop_ends_on_a_tool_less_answer(self):
        looping = tool_reply(call("c1"))
        model = ScriptedModel([looping] * 4 + [reply("done")])

        run = _run(model)

        self.assertEqual(run.response.content, "done")
        self.assertIsNone(model.last_tools)
        self.assertTrue(run.rounds[-2]["repeat_loop_stopped"])
        # Asked on every round the run made, each of them once.
        self.assertEqual(
            [call_id for call_id, _ in tool_results(_sent(model))], ["c1"] * 4
        )

    def test_same_tool_with_different_arguments_is_not_a_repeat(self):
        model = ScriptedModel(
            [
                tool_reply(call(f"c{i}", arguments=f'{{"url": "https://{i}.com"}}'))
                for i in range(4)
            ]
            + [reply("done")]
        )
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset)

        self.assertEqual(len(toolset.executed), 4)
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "done")

    def test_a_round_mixing_a_repeat_and_new_work_continues(self):
        repeated = '{"url": "https://a.com"}'
        model = ScriptedModel(
            [
                tool_reply(call("c1", arguments=repeated)),
                tool_reply(call("c2", arguments=repeated)),
                tool_reply(call("c3", arguments=repeated)),
                tool_reply(
                    call("c4", arguments=repeated),
                    call("c5", arguments='{"url": "https://b.com"}'),
                ),
                reply("done"),
            ]
        )
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset)

        # The duplicate is refused, its sibling still runs, the loop goes on.
        self.assertEqual(len(toolset.executed), 4)
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "done")


class CallsTheLibraryAnswersTests(TestCase):
    """A call that never reaches a tool still gets a result, and never ends the run."""

    def test_an_unknown_tool_is_recorded_as_a_failure_the_model_reads(self):
        model = ScriptedModel([tool_reply(call("c1", name="nope")), reply("done")])

        run = _run(model)

        self.assertIs(run.stop, StopReason.ANSWERED)
        recorded = run.tool_data[0]["results"][0]
        self.assertEqual(recorded["tool_call_id"], "c1")
        self.assertIn("nope", parse_tool_failure(recorded["content"])["reason"])
        self.assertIn("nope", tool_results(_sent(model))[0][1])

    def test_arguments_that_are_not_json_are_recorded_as_a_failure(self):
        model = ScriptedModel(
            [tool_reply(call("c1", arguments="{not json")), reply("done")]
        )
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset)

        self.assertEqual(toolset.executed, [])
        self.assertIsNotNone(
            parse_tool_failure(run.tool_data[0]["results"][0]["content"])
        )

    def test_a_model_stuck_on_an_unknown_tool_is_stopped_like_any_repeat(self):
        model = ScriptedModel([tool_reply(call("c1", name="nope"))] * 4 + [reply("ok")])

        run = _run(model, max_identical_calls=3)

        self.assertIs(run.stop, StopReason.REPEAT_LOOP)
        self.assertEqual(len(model.requests), 5)

    def test_wrong_calls_never_exhaust_a_budget_of_the_library(self):
        args = itertools.count()
        model = ScriptedModel(
            lambda messages, tools: tool_reply(
                call("c1", name="nope", arguments=f'{{"q":{next(args)}}}')
            )
        )

        run = _run(model, max_rounds=60)

        # Past the library's default caps on retries and on requests.
        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertEqual(len(model.requests), 62)


class TextToolCallTests(TestCase):
    def test_a_call_written_out_as_json_is_run(self):
        model = ScriptedModel(
            [reply('{"name": "search", "arguments": {"q": "cats"}}'), reply("done")]
        )
        seen = []
        toolset = StubToolset(
            handler=lambda tc, ctx: seen.append(json.loads(tc.arguments)) or "ok",
            definitions=("search",),
        )

        run = _run(model, toolset)

        self.assertEqual(seen, [{"q": "cats"}])
        self.assertEqual(run.response.content, "done")
        self.assertEqual(run.tool_data[0]["assistant_content"], "")

    def test_json_in_a_forced_answer_is_the_answer(self):
        looping = tool_reply(call("c1"))
        answer = '{"name": "search", "arguments": {}}'
        model = ScriptedModel([looping, looping, reply(answer)])
        toolset = StubToolset(handler=lambda tc, ctx: "ok", definitions=("search",))

        run = _run(model, toolset, max_rounds=1)

        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertEqual(run.response.content, answer)
        self.assertEqual(len(toolset.executed), 1)


class ResultTruncationTests(TestCase):
    def test_replayable_history_keeps_far_more_than_the_debug_record(self):
        page = "HEAD" + ("p" * 20000) + "TAIL"
        model = ScriptedModel(
            [
                tool_reply(
                    call(
                        "c1",
                        name="fetch_url",
                        arguments='{"url": "https://example.com/doc"}',
                    )
                ),
                reply("done"),
            ]
        )
        toolset = StubToolset(
            handler=lambda tc, ctx: page,
            definitions=("fetch_url",),
            describe=lambda name, args: "fetch_url(https://example.com/doc)",
        )

        run = _run(model, toolset, task_max_chars=300, store_max_chars=3000)

        debug_result = run.rounds[0]["tool_executions"][0]["result"]
        replayed = run.tool_data[0]["results"][0]["content"]
        self.assertLessEqual(len(debug_result), 300)
        self.assertLessEqual(len(replayed), 3000)
        self.assertGreater(len(replayed), len(debug_result))
        for text in (debug_result, replayed):
            self.assertTrue(text.startswith("HEAD"))
            self.assertTrue(text.endswith("TAIL"))
            self.assertIn("fetch_url(https://example.com/doc)", text)
        # The model itself read the page whole.
        self.assertEqual(tool_results(_sent(model)), [("c1", page)])

    def test_an_image_result_reaches_the_model_as_a_picture(self):
        payload = '{"type": "image", "data": "AAAA", "text": "a cat"}'
        model = ScriptedModel(
            [tool_reply(call("c1", name="generate_image")), reply("done")]
        )
        toolset = StubToolset(
            handler=lambda tc, ctx: payload, definitions=("generate_image",)
        )

        run = _run(model, toolset)

        text, image = tool_results(_sent(model))[0][1]
        self.assertEqual(text, "a cat")
        self.assertEqual(image.data, b"\x00\x00\x00")
        self.assertEqual(run.tool_data[0]["results"][0]["content"], "a cat")
        self.assertNotIn("AAAA", run.rounds[0]["tool_executions"][0]["result"])


class ObserverFailureTests(TestCase):
    def test_a_failing_on_stop_observer_leaves_the_result_alone(self):
        class Broken(Observer):
            def on_stop(self, run):
                raise RuntimeError("stop")

        model = ScriptedModel([reply("hello")])

        with self.assertLogs("workspace.ai.harness.observers", level="ERROR"):
            run = _run(model, observers=[Broken()])

        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "hello")


class ToolFailureTests(TestCase):
    def test_a_bug_in_a_tool_fails_the_run(self):
        def handler(tool_call, context):
            raise RuntimeError("tool exploded")

        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)

        with self.assertRaisesMessage(RuntimeError, "tool exploded"):
            _run(model, StubToolset(handler=handler, definitions=("search",)))
