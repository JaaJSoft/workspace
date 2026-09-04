import itertools

from django.test import TestCase

from workspace.ai.harness.observers import Observer
from workspace.ai.harness.runner import StopReason

from .harness import (
    ScriptedModel,
    StubToolset,
    build_runner,
    call,
    reply,
    tool_messages,
    tool_reply,
)


def _run(model, toolset=None, **limits):
    messages = [{"role": "user", "content": "go"}]
    toolset = toolset or StubToolset(handler=lambda tc, ctx: "ok")
    runner = build_runner(model, toolset, **limits)
    return runner.run(messages), messages


class RoundCapTests(TestCase):
    def test_loop_stops_at_configured_round_cap(self):
        model = ScriptedModel([tool_reply(call("call_1"))], repeat=True)
        toolset = StubToolset(handler=lambda tc, ctx: "ok")

        run, _ = _run(model, toolset, max_rounds=2)

        # 1 initial call + one re-call per allowed round + the tool-less call
        # that turns the exhausted run into an answer
        self.assertEqual(len(model.requests), 4)
        self.assertEqual(len(toolset.executed), 2)
        self.assertIs(run.stop, StopReason.ROUND_CAP)
        self.assertIsNone(model.last_tools)
        # The final entry is the captured last response, not a tool round
        self.assertEqual(len(run.rounds), 4)
        self.assertTrue(run.rounds[-2]["round_cap_reached"])
        self.assertNotIn("tool_executions", run.rounds[-1])

    def test_no_extra_call_when_cap_lands_on_a_text_answer(self):
        looping = tool_reply(call("call_1"))
        model = ScriptedModel([looping, looping, reply("done")])

        run, _ = _run(model, max_rounds=2)

        self.assertEqual(len(model.requests), 3)
        self.assertEqual(run.response.content, "done")
        # The cap was spent, but the run ended on a real answer: nothing was
        # cut short, so it does not count as one.
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertNotIn("round_cap_reached", run.rounds[-1])

    def test_tools_are_offered_on_every_round_but_the_forced_answer(self):
        model = ScriptedModel([tool_reply(call("call_1"))], repeat=True)
        toolset = StubToolset(definitions=("search",))

        _run(model, toolset, max_rounds=1)

        self.assertEqual(
            [tools is not None for _, tools in model.requests], [True, True, False]
        )
        self.assertEqual(model.requests[0][1], toolset.get_definitions())


class StopAfterRoundTests(TestCase):
    def test_stop_after_round_halts_loop(self):
        def halt(tool_call, context):
            context["stop_after_round"] = True
            return "ok"

        model = ScriptedModel(
            [tool_reply(call("call_1", name="halt_tool"))], repeat=True
        )

        run, _ = _run(model, StubToolset(handler=halt))

        self.assertEqual(len(model.requests), 1)
        self.assertIs(run.stop, StopReason.AWAITING_USER)
        self.assertTrue(run.context["stop_after_round"])
        self.assertTrue(run.rounds[-1]["terminated_by_tool"])


class AnsweredTests(TestCase):
    def test_a_reply_without_tools_ends_the_run_at_once(self):
        model = ScriptedModel([reply("hello")])

        run, messages = _run(model)

        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "hello")
        self.assertEqual(run.rounds, [{"response": reply("hello").as_record()}])
        self.assertIsNone(run.tool_data)
        self.assertEqual(messages, [{"role": "user", "content": "go"}])

    def test_the_conversation_carries_each_round(self):
        model = ScriptedModel(
            [
                tool_reply(
                    call("c1", arguments='{"q": 1}'), content="looking", thinking="hmm"
                ),
                reply("done"),
            ]
        )

        run, messages = _run(model)

        self.assertEqual(
            messages[1],
            {
                "role": "assistant",
                "content": "looking",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q": 1}'},
                    }
                ],
            },
        )
        self.assertEqual(tool_messages(messages), [("c1", "ok")])
        # What the round said, without its reasoning: that is kept apart.
        self.assertEqual(run.tool_data[0]["assistant_content"], "looking")
        self.assertEqual(run.tool_data[0]["thinking"], "hmm")
        self.assertEqual(
            run.tool_data[0]["results"], [{"tool_call_id": "c1", "content": "ok"}]
        )
        self.assertEqual(run.rounds[0]["tool_executions"][0]["name"], "search")

    def test_retry_final_asks_again_without_tools_and_runs_nothing(self):
        model = ScriptedModel([tool_reply(call("c1")), reply(""), reply("done")])
        toolset = StubToolset(handler=lambda tc, ctx: "ok")
        messages = [{"role": "user", "content": "go"}]
        runner = build_runner(model, toolset)
        run = runner.run(messages)

        retried = runner.retry_final(messages, run)

        self.assertEqual(retried.response.content, "done")
        self.assertIsNone(model.last_tools)
        self.assertEqual(len(toolset.executed), 1)
        self.assertEqual(len(retried.rounds), 3)
        self.assertIs(retried.rounds, run.rounds)


class ThinkingPersistenceTests(TestCase):
    def test_round_thinking_is_stored_in_tool_data(self):
        model = ScriptedModel(
            [
                tool_reply(call("call_1"), thinking="I should search first"),
                reply("Done", thinking="now I can answer"),
            ]
        )

        run, _ = _run(model)

        self.assertEqual(len(run.tool_data), 1)
        self.assertEqual(run.tool_data[0]["thinking"], "I should search first")
        # Final-response thinking is NOT in tool_data (post_bot_message appends it)
        self.assertEqual(run.response.thinking, "now I can answer")


class CancellationTests(TestCase):
    def test_a_cancellation_read_after_the_round_buys_no_more_model_call(self):
        cancelled = []

        def cancel(tool_call, context):
            cancelled.append(True)
            return "ok"

        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)

        run, _ = _run(
            model, StubToolset(handler=cancel), is_cancelled=lambda: bool(cancelled)
        )

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(len(model.requests), 1)
        self.assertTrue(run.rounds[-1]["cancelled"])

    def test_a_run_cancelled_before_any_tool_records_the_round(self):
        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)

        run, messages = _run(model, is_cancelled=lambda: True)

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(tool_messages(messages), [])
        self.assertEqual(run.tool_data[0]["results"], [])


class RepeatedToolCallTests(TestCase):
    def test_identical_call_runs_at_most_the_configured_number_of_times(self):
        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)
        toolset = StubToolset(handler=lambda tc, ctx: "ok")

        run, _ = _run(model, toolset, max_identical_calls=3)

        self.assertEqual(len(toolset.executed), 3)
        self.assertIs(run.stop, StopReason.REPEAT_LOOP)

    def test_refused_call_reports_why_to_the_model(self):
        model = ScriptedModel([tool_reply(call("c1"))], repeat=True)

        run, messages = _run(model)

        refused = run.rounds[3]["tool_executions"][0]["result"]
        self.assertIn("Not executed", refused)
        self.assertIn("search", refused)
        self.assertEqual(tool_messages(messages)[-1][1], refused)

    def test_repeat_loop_ends_on_a_tool_less_answer(self):
        looping = tool_reply(call("c1"))
        model = ScriptedModel([looping] * 4 + [reply("done")])

        run, _ = _run(model)

        self.assertEqual(run.response.content, "done")
        self.assertIsNone(model.last_tools)
        self.assertTrue(run.rounds[-2]["repeat_loop_stopped"])

    def test_same_tool_with_different_arguments_is_not_a_repeat(self):
        model = ScriptedModel(
            [
                tool_reply(call(f"c{i}", arguments=f'{{"url": "https://{i}.com"}}'))
                for i in range(4)
            ]
            + [reply("done")]
        )
        toolset = StubToolset(handler=lambda tc, ctx: "ok")

        run, _ = _run(model, toolset)

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
        toolset = StubToolset(handler=lambda tc, ctx: "ok")

        run, _ = _run(model, toolset)

        # The duplicate is refused, its sibling still runs, the loop goes on.
        self.assertEqual(len(toolset.executed), 4)
        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "done")


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
            describe=lambda name, args: "fetch_url(https://example.com/doc)",
        )

        run, _ = _run(model, toolset, task_max_chars=300, store_max_chars=3000)

        debug_result = run.rounds[0]["tool_executions"][0]["result"]
        replayed = run.tool_data[0]["results"][0]["content"]
        self.assertLessEqual(len(debug_result), 300)
        self.assertLessEqual(len(replayed), 3000)
        self.assertGreater(len(replayed), len(debug_result))
        # Both are cut in the middle and point back at the call that made them.
        for text in (debug_result, replayed):
            self.assertTrue(text.startswith("HEAD"))
            self.assertTrue(text.endswith("TAIL"))
            self.assertIn("fetch_url(https://example.com/doc)", text)

    def test_an_image_result_is_replayed_as_its_text(self):
        payload = '{"type": "image", "data": "AAAA", "text": "a cat"}'
        model = ScriptedModel(
            [tool_reply(call("c1", name="generate_image")), reply("done")]
        )

        run, messages = _run(model, StubToolset(handler=lambda tc, ctx: payload))

        self.assertIsInstance(messages[2]["content"], list)
        self.assertEqual(run.tool_data[0]["results"][0]["content"], "a cat")
        self.assertNotIn("AAAA", run.rounds[0]["tool_executions"][0]["result"])


class ObserverFailureTests(TestCase):
    def test_a_failing_on_stop_observer_leaves_the_result_alone(self):
        class Broken(Observer):
            def on_stop(self, run):
                raise RuntimeError("stop")

        model = ScriptedModel([reply("hello")])

        with self.assertLogs("workspace.ai.harness.observers", level="ERROR"):
            run, _ = _run(model, observers=[Broken()])

        self.assertIs(run.stop, StopReason.ANSWERED)
        self.assertEqual(run.response.content, "hello")


class RoundCapMetricsShapeTests(TestCase):
    def test_distinct_arguments_each_round_exhaust_the_cap(self):
        # Distinct arguments each round, so the repeat guard never fires and
        # the run dies on the round cap instead.
        args = itertools.count()
        model = ScriptedModel(
            lambda messages, tools: tool_reply(
                call("c1", arguments=f'{{"q":{next(args)}}}')
            )
        )

        run, _ = _run(model, max_rounds=2)

        self.assertIs(run.stop, StopReason.ROUND_CAP)
