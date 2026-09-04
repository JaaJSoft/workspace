import threading

from django.test import TestCase

from workspace.ai.harness.dispatch import Dispatcher
from workspace.ai.harness.observers import Observer
from workspace.ai.harness.policies import RepeatGuard
from workspace.ai.services.call_order import call_position

from .harness import StubToolset, call


class ConcurrentToolCallTests(TestCase):
    """Independent calls of one round share a dispatch; everything else doesn't.

    Handlers run for real, in a pool thread when the dispatcher batches
    them, so the recorded spans are the actual execution order rather than
    a reconstruction of it. The width is stated on every dispatcher: a test
    that proves two calls overlap has to say what it needs, or a changed
    default turns it into a barrier timeout that names nothing.
    """

    def _dispatcher(
        self, toolset, *, concurrency=4, is_cancelled=None, context=None, observers=()
    ):
        return Dispatcher(
            toolset,
            concurrency=concurrency,
            user=None,
            bot=None,
            context=context,
            is_cancelled=is_cancelled,
            policies=[RepeatGuard(3)],
            observers=observers,
        )

    def _read(self, call_id, url=None):
        return call(
            "c" + str(call_id) if isinstance(call_id, int) else call_id,
            name="read_webpage",
            arguments=f'{{"url": "https://{url or call_id}.test"}}',
        )

    def test_reads_in_one_round_run_at_the_same_time(self):
        # Both calls must be in flight together for the barrier to release;
        # a sequential dispatcher breaks it and the run fails instead of
        # passing slowly.
        barrier = threading.Barrier(2, timeout=10)
        toolset = StubToolset(
            concurrent={"read_webpage"},
            handler=lambda tc, ctx: barrier.wait() and "" or "read",
        )

        self._dispatcher(toolset).run_round(
            [self._read("c1", "a"), self._read("c2", "b")]
        )

        self.assertEqual(toolset.peak_in_flight, 2)

    def test_results_follow_call_order_not_completion_order(self):
        finished_second = threading.Event()

        def handler(tool_call, context):
            if tool_call.id == "c1":
                finished_second.wait(10)
                return "first"
            finished_second.set()
            return "second"

        toolset = StubToolset(concurrent={"read_webpage"}, handler=handler)

        outcome = self._dispatcher(toolset).run_round(
            [self._read("c1", "a"), self._read("c2", "b")]
        )

        # c2 finished first, yet the results read in the order asked for.
        self.assertEqual(toolset.spans[-2], ("c2", "end"))
        self.assertEqual(
            [(o.call.id, o.result) for o in outcome.outcomes],
            [("c1", "first"), ("c2", "second")],
        )

    def test_each_call_knows_where_it_belongs_in_the_reply(self):
        # What a tool leaving an image for the caller stamps on it: the rank
        # the model asked for, not the rank it came back in.
        seen = {}

        def handler(tool_call, context):
            seen[tool_call.id] = call_position()
            return "ok"

        toolset = StubToolset(concurrent={"generate_image"}, handler=handler)
        calls = [
            call(f"c{i}", name="generate_image", arguments=f'{{"prompt": "{i}"}}')
            for i in range(1, 4)
        ]

        outcome = self._dispatcher(toolset).run_round(calls)

        self.assertEqual(seen, {"c1": 1, "c2": 2, "c3": 3})
        self.assertEqual([o.position for o in outcome.outcomes], [1, 2, 3])

    def test_the_rank_keeps_climbing_across_rounds(self):
        # Images of a later round are attached after those of an earlier one,
        # so the rank cannot restart with each round.
        seen = {}

        def handler(tool_call, context):
            seen[tool_call.id] = call_position()
            return "ok"

        toolset = StubToolset(concurrent={"generate_image"}, handler=handler)
        dispatcher = self._dispatcher(toolset)

        dispatcher.run_round(
            [
                call(f"c{i}", name="generate_image", arguments=f'{{"prompt": "{i}"}}')
                for i in (1, 2)
            ]
        )
        dispatcher.run_round(
            [call("c3", name="generate_image", arguments='{"prompt": "3"}')]
        )

        self.assertEqual(seen, {"c1": 1, "c2": 2, "c3": 3})

    def test_a_write_splits_the_round_and_keeps_its_place(self):
        toolset = StubToolset(concurrent={"read_webpage"})
        calls = [
            self._read("r1", "a"),
            call("w1", name="send_user_message", arguments="{}"),
            self._read("r2", "b"),
        ]

        outcome = self._dispatcher(toolset).run_round(calls)

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
        self.assertEqual([o.call.id for o in outcome.outcomes], ["r1", "w1", "r2"])

    def test_a_batch_never_grows_past_the_configured_width(self):
        toolset = StubToolset(concurrent={"read_webpage"})
        calls = [self._read(f"c{i}", str(i)) for i in range(5)]

        self._dispatcher(toolset, concurrency=2).run_round(calls)

        self.assertLessEqual(toolset.peak_in_flight, 2)
        # The third call waits for the pair before it: batches are dispatched
        # one after another, only their members overlap.
        self.assertEqual(toolset.executed, ["c0", "c1", "c2", "c3", "c4"])
        third_start = toolset.spans.index(("c2", "start"))
        self.assertLess(toolset.spans.index(("c0", "end")), third_start)
        self.assertLess(toolset.spans.index(("c1", "end")), third_start)

    def test_concurrency_of_one_runs_reads_one_by_one(self):
        toolset = StubToolset(concurrent={"read_webpage"})

        self._dispatcher(toolset, concurrency=1).run_round(
            [self._read("c1", "a"), self._read("c2", "b")]
        )

        self.assertEqual(toolset.peak_in_flight, 1)

    def test_a_cancellation_stops_the_next_batch_from_starting(self):
        cancelled = threading.Event()

        def handler(tool_call, context):
            if tool_call.id == "c2":
                cancelled.set()
            return "ok"

        toolset = StubToolset(concurrent={"read_webpage"}, handler=handler)
        calls = [self._read(f"c{i}", str(i)) for i in range(1, 5)]

        outcome = self._dispatcher(
            toolset, concurrency=2, is_cancelled=cancelled.is_set
        ).run_round(calls)

        self.assertTrue(outcome.cancelled)
        self.assertEqual([o.call.id for o in outcome.outcomes], ["c1", "c2"])

    def test_a_cancelled_run_dispatches_nothing(self):
        toolset = StubToolset(concurrent={"read_webpage"})

        outcome = self._dispatcher(toolset, is_cancelled=lambda: True).run_round(
            [self._read("c1", "a"), self._read("c2", "b")]
        )

        self.assertTrue(outcome.cancelled)
        self.assertEqual(outcome.outcomes, [])
        self.assertEqual(toolset.spans, [])

    def test_a_repeated_call_is_still_refused_inside_a_batch(self):
        toolset = StubToolset(concurrent={"read_webpage"})
        calls = [self._read(f"c{i}", "a") for i in range(1, 5)] + [
            self._read("c5", "b")
        ]

        outcome = self._dispatcher(toolset).run_round(calls)

        self.assertEqual(sorted(toolset.executed), ["c1", "c2", "c3", "c5"])
        self.assertEqual(
            [o.call.id for o in outcome.outcomes], ["c1", "c2", "c3", "c4", "c5"]
        )
        refused = outcome.outcomes[3]
        self.assertTrue(refused.refused)
        self.assertIn("Not executed", refused.refusal)
        self.assertEqual(outcome.executed, 4)

    def test_a_round_of_nothing_but_refusals_executed_nothing(self):
        toolset = StubToolset()
        dispatcher = self._dispatcher(toolset)
        for _ in range(3):
            dispatcher.run_round([call("c", arguments='{"a": 1}')])

        outcome = dispatcher.run_round([call("c", arguments='{"a": 1}')])

        self.assertEqual(outcome.executed, 0)
        self.assertEqual(len(toolset.executed), 3)

    def test_a_failing_call_takes_the_batch_down_with_it(self):
        def handler(tool_call, context):
            if tool_call.id == "c2":
                raise RuntimeError("tool exploded")
            return "ok"

        toolset = StubToolset(concurrent={"read_webpage"}, handler=handler)

        with self.assertRaises(RuntimeError):
            self._dispatcher(toolset).run_round(
                [self._read("c1", "a"), self._read("c2", "b")]
            )

    def test_the_run_context_reaches_the_handler(self):
        context = {"agent_checkin": True}
        seen = {}

        def handler(tool_call, ctx):
            seen.update(ctx)
            ctx["images"] = ["x"]
            return "ok"

        toolset = StubToolset(handler=handler)

        self._dispatcher(toolset, context=context).run_round([call("c1")])

        self.assertEqual(seen, {"agent_checkin": True})
        self.assertEqual(context["images"], ["x"])

    def test_a_failing_observer_never_fails_the_call(self):
        # An observer only watches: whichever hook it breaks in, the call it
        # was watching still counts as having run, with its own result.
        class Broken(Observer):
            def on_call_start(self, call):
                raise RuntimeError("start")

            def on_call_return(self, call):
                raise RuntimeError("return")

            def on_call_end(self, outcome):
                raise RuntimeError("end")

        toolset = StubToolset(concurrent={"read_webpage"})

        with self.assertLogs("workspace.ai.harness.observers", level="ERROR") as logs:
            outcome = self._dispatcher(toolset, observers=[Broken()]).run_round(
                [self._read("c1", "a"), self._read("c2", "b")]
            )

        self.assertEqual(
            [(o.call.id, o.result, o.error) for o in outcome.outcomes],
            [("c1", "result c1", None), ("c2", "result c2", None)],
        )
        self.assertEqual(len(logs.records), 6)
