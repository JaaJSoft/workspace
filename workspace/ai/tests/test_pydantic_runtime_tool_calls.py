"""How the calls of a round run on the Pydantic AI runtime.

The twin of ``test_harness_dispatch.py``: handlers run for real, on the
thread the runtime gives them, so the recorded spans are the actual
execution order rather than a reconstruction of it.
"""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import translation

from workspace.ai.harness.observers import Observer
from workspace.ai.harness.runner import StopReason
from workspace.ai.services.call_order import call_position
from workspace.ai.tool_registry import parse_tool_failure
from workspace.users.services.settings import set_setting

from .harness import ScriptedModel, StubToolset, call, reply, tool_reply
from .pydantic_runtime import build_runner, tool_results

User = get_user_model()

TOOLS = ("read_webpage", "generate_image", "send_user_message")


def _read(call_id, url=None):
    return call(
        call_id,
        name="read_webpage",
        arguments=f'{{"url": "https://{url or call_id}.test"}}',
    )


def _toolset(handler=None, concurrent=("read_webpage",)):
    return StubToolset(concurrent=concurrent, handler=handler, definitions=TOOLS)


def _run_round(toolset, calls, **limits):
    """One reply asking for *calls*, then an answer."""
    model = ScriptedModel([tool_reply(*calls), reply("done")])
    run = build_runner(model, toolset, **limits).run(
        [{"role": "user", "content": "go"}]
    )
    return run, model


class ConcurrentToolCallTests(TestCase):
    def test_reads_in_one_round_run_at_the_same_time(self):
        # Both calls must be in flight together for the barrier to release.
        barrier = threading.Barrier(2, timeout=10)
        toolset = _toolset(handler=lambda tc, ctx: barrier.wait() and "" or "read")

        _run_round(toolset, [_read("c1", "a"), _read("c2", "b")])

        self.assertEqual(toolset.peak_in_flight, 2)

    def test_results_follow_call_order_not_completion_order(self):
        finished_second = threading.Event()

        def handler(tool_call, context):
            if tool_call.id == "c1":
                finished_second.wait(10)
                return "first"
            finished_second.set()
            return "second"

        toolset = _toolset(handler=handler)

        run, model = _run_round(toolset, [_read("c1", "a"), _read("c2", "b")])

        # c2 finished first, yet the results read in the order asked for.
        self.assertEqual(toolset.spans[-2], ("c2", "end"))
        self.assertEqual(
            tool_results(model.requests[-1][0]), [("c1", "first"), ("c2", "second")]
        )
        self.assertEqual(
            run.tool_data[0]["results"],
            [
                {"tool_call_id": "c1", "content": "first"},
                {"tool_call_id": "c2", "content": "second"},
            ],
        )

    def test_each_call_knows_where_it_belongs_in_the_reply(self):
        seen = {}

        def handler(tool_call, context):
            seen[tool_call.id] = call_position()
            return "ok"

        toolset = _toolset(handler=handler, concurrent=("generate_image",))
        calls = [
            call(f"c{i}", name="generate_image", arguments=f'{{"prompt": "{i}"}}')
            for i in range(1, 4)
        ]

        _run_round(toolset, calls)

        self.assertEqual(seen, {"c1": 1, "c2": 2, "c3": 3})

    def test_the_rank_keeps_climbing_across_rounds(self):
        seen = {}

        def handler(tool_call, context):
            seen[tool_call.id] = call_position()
            return "ok"

        toolset = _toolset(handler=handler, concurrent=("generate_image",))
        image = lambda i: call(  # noqa: E731
            f"c{i}", name="generate_image", arguments=f'{{"prompt": "{i}"}}'
        )
        model = ScriptedModel(
            [tool_reply(image(1), image(2)), tool_reply(image(3)), reply("done")]
        )

        build_runner(model, toolset).run([{"role": "user", "content": "go"}])

        self.assertEqual(seen, {"c1": 1, "c2": 2, "c3": 3})

    def test_a_write_splits_the_round_and_keeps_its_place(self):
        toolset = _toolset()
        calls = [
            _read("r1", "a"),
            call("w1", name="send_user_message", arguments="{}"),
            _read("r2", "b"),
        ]

        _, model = _run_round(toolset, calls)

        self.assertEqual(
            toolset.spans,
            [
                ("r1", "start"),
                ("r1", "end"),
                ("w1", "start"),
                ("w1", "end"),
                ("r2", "start"),
                ("r2", "end"),
            ],
        )
        self.assertEqual(
            [call_id for call_id, _ in tool_results(model.requests[-1][0])],
            ["r1", "w1", "r2"],
        )

    def test_no_more_calls_in_flight_than_the_configured_width(self):
        release = threading.Event()

        def handler(tool_call, context):
            release.wait(0.2)
            return "ok"

        toolset = _toolset(handler=handler)
        calls = [_read(f"c{i}", str(i)) for i in range(5)]

        _run_round(toolset, calls, concurrency=2)

        self.assertEqual(toolset.peak_in_flight, 2)
        self.assertEqual(sorted(toolset.executed), ["c0", "c1", "c2", "c3", "c4"])

    def test_concurrency_of_one_runs_reads_one_by_one(self):
        toolset = _toolset()

        _run_round(toolset, [_read("c1", "a"), _read("c2", "b")], concurrency=1)

        self.assertEqual(toolset.peak_in_flight, 1)
        self.assertEqual(toolset.executed, ["c1", "c2"])

    def test_a_repeated_call_is_still_refused_among_its_neighbours(self):
        toolset = _toolset()
        calls = [_read(f"c{i}", "a") for i in range(1, 5)] + [_read("c5", "b")]

        run, model = _run_round(toolset, calls)

        self.assertEqual(sorted(toolset.executed), ["c1", "c2", "c3", "c5"])
        results = tool_results(model.requests[-1][0])
        self.assertEqual(
            [call_id for call_id, _ in results], [f"c{i}" for i in range(1, 6)]
        )
        self.assertIn("not executed", parse_tool_failure(results[3][1])["reason"])
        self.assertIs(run.stop, StopReason.ANSWERED)

    def test_the_run_context_reaches_the_handler(self):
        context = {"agent_checkin": True}
        seen = {}

        def handler(tool_call, ctx):
            seen.update(ctx)
            ctx["images"] = ["x"]
            return "ok"

        run, _ = _run_round(_toolset(handler=handler), [_read("c1")], context=context)

        self.assertEqual(seen, {"agent_checkin": True})
        self.assertEqual(run.context["images"], ["x"])


class ThreadTests(TestCase):
    """Which thread a handler runs on decides which database connection it gets."""

    def _threads(self, calls, **limits):
        threads = {}

        def handler(tool_call, context):
            threads[tool_call.id] = threading.get_ident()
            return "ok"

        _run_round(_toolset(handler=handler), calls, **limits)
        return threads

    def test_a_tool_that_runs_alone_runs_on_the_thread_that_started_the_run(self):
        # Where it finds the task's connection, and in a test the open
        # transaction holding the rows the test created.
        threads = self._threads([call("w1", name="send_user_message")])

        self.assertEqual(threads, {"w1": threading.get_ident()})

    def test_a_lone_read_stays_on_that_thread_too(self):
        threads = self._threads([_read("c1")])

        self.assertEqual(threads, {"c1": threading.get_ident()})

    def test_reads_that_run_together_each_take_a_pool_thread(self):
        threads = self._threads([_read("c1", "a"), _read("c2", "b")])

        self.assertNotIn(threading.get_ident(), threads.values())

    def test_a_refused_neighbour_leaves_a_read_alone_on_the_task_thread(self):
        threads = {}

        def handler(tool_call, context):
            threads[tool_call.id] = threading.get_ident()
            return "ok"

        model = ScriptedModel(
            [
                tool_reply(_read("c1", "a")),
                tool_reply(_read("c2", "a"), _read("c3", "b")),
                reply("done"),
            ]
        )

        build_runner(model, _toolset(handler=handler), max_identical_calls=1).run(
            [{"role": "user", "content": "go"}]
        )

        self.assertEqual(threads["c3"], threading.get_ident())

    def test_a_pool_thread_hands_its_database_connections_back(self):
        with patch(
            "workspace.ai.harness.pydantic_runtime.tool_calls.connections"
        ) as connections:
            self._threads([_read("c1", "a"), _read("c2", "b")])

        self.assertEqual(connections.close_all.call_count, 2)

    def test_the_task_thread_keeps_its_connection(self):
        with patch(
            "workspace.ai.harness.pydantic_runtime.tool_calls.connections"
        ) as connections:
            self._threads([call("w1", name="send_user_message"), _read("c1")])

        connections.close_all.assert_not_called()


class CancellationTests(TestCase):
    def test_a_cancellation_keeps_the_calls_after_it_from_starting(self):
        cancelled = threading.Event()

        def handler(tool_call, context):
            cancelled.set()
            return "ok"

        toolset = _toolset(handler=handler, concurrent=())
        calls = [_read(f"c{i}", str(i)) for i in range(1, 4)]

        run, model = _run_round(toolset, calls, is_cancelled=cancelled.is_set)

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertEqual(toolset.executed, ["c1"])
        self.assertEqual(
            [result["tool_call_id"] for result in run.tool_data[0]["results"]], ["c1"]
        )
        self.assertEqual(len(model.requests), 1)

    def test_a_call_waiting_for_a_slot_reads_the_cancellation_before_it_runs(self):
        cancelled = threading.Event()

        def handler(tool_call, context):
            cancelled.set()
            return "ok"

        toolset = _toolset(handler=handler)
        calls = [_read(f"c{i}", str(i)) for i in range(1, 5)]

        run, _ = _run_round(
            toolset, calls, concurrency=2, is_cancelled=cancelled.is_set
        )

        self.assertIs(run.stop, StopReason.CANCELLED)
        self.assertLessEqual(len(toolset.executed), 2)


class ObserverTests(TestCase):
    def _recording(self):
        events = []

        class Recording(Observer):
            def on_call_start(self, call):
                events.append(("start", call.id))

            def on_call_return(self, call):
                events.append(("return", call.id))

            def on_call_end(self, outcome):
                events.append(("end", outcome.call.id, outcome.refused))

        return Recording(), events

    def test_calls_are_announced_and_read_back_in_the_order_asked_for(self):
        observer, events = self._recording()
        calls = [_read(f"c{i}", str(i)) for i in range(1, 4)]

        _run_round(_toolset(), calls, observers=[observer])

        self.assertEqual(
            [event for event in events if event[0] == "start"],
            [("start", "c1"), ("start", "c2"), ("start", "c3")],
        )
        self.assertEqual(
            events[-3:],
            [("end", "c1", False), ("end", "c2", False), ("end", "c3", False)],
        )
        self.assertEqual(
            sorted(event for event in events if event[0] == "return"),
            [("return", "c1"), ("return", "c2"), ("return", "c3")],
        )

    def test_a_refused_repeat_is_read_back_but_never_announced(self):
        observer, events = self._recording()
        calls = [_read("c1", "a"), _read("c2", "a")]

        _run_round(_toolset(), calls, observers=[observer], max_identical_calls=1)

        self.assertEqual(
            events,
            [
                ("start", "c1"),
                ("return", "c1"),
                ("end", "c1", False),
                ("end", "c2", True),
            ],
        )

    def test_a_failing_observer_never_fails_the_call(self):
        class Broken(Observer):
            def on_call_start(self, call):
                raise RuntimeError("start")

            def on_call_return(self, call):
                raise RuntimeError("return")

            def on_call_end(self, outcome):
                raise RuntimeError("end")

        with self.assertLogs("workspace.ai.harness.observers", level="ERROR") as logs:
            run, model = _run_round(
                _toolset(), [_read("c1", "a"), _read("c2", "b")], observers=[Broken()]
            )

        self.assertEqual(
            tool_results(model.requests[-1][0]),
            [("c1", "result c1"), ("c2", "result c2")],
        )
        self.assertEqual(len(logs.records), 6)

    def test_a_tool_that_raises_is_read_back_with_its_error(self):
        observer, events = self._recording()
        errors = []
        observer.on_call_end = lambda outcome: errors.append(outcome.error)

        def handler(tool_call, context):
            raise RuntimeError("tool exploded")

        with self.assertRaises(RuntimeError):
            _run_round(_toolset(handler=handler), [_read("c1")], observers=[observer])

        self.assertEqual([str(error) for error in errors], ["tool exploded"])

    def test_a_tool_that_raises_beside_others_still_fails_the_run(self):
        def handler(tool_call, context):
            if tool_call.id == "c2":
                raise RuntimeError("tool exploded")
            return "ok"

        with self.assertRaises(RuntimeError):
            _run_round(_toolset(handler=handler), [_read("c1", "a"), _read("c2", "b")])


class LanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        set_setting(self.user, "core", "language", "fr")

    def tearDown(self):
        cache.clear()

    def test_a_handler_runs_in_the_users_language_on_either_thread(self):
        languages = {}

        def handler(tool_call, context):
            languages[tool_call.id] = translation.get_language()
            return "ok"

        calls = [
            call("w1", name="send_user_message"),
            _read("c1", "a"),
            _read("c2", "b"),
        ]

        _run_round(_toolset(handler=handler), calls, user=self.user)

        self.assertEqual(languages, {"w1": "fr", "c1": "fr", "c2": "fr"})
