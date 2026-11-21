#!/usr/bin/env python
"""Run a bot through fixed scenarios on a real model, on each agent runtime.

A unit test can say that the loop stops where it should. It cannot say
that a bot still picks the right tool, or still stops for a confirmation,
after a prompt was rewritten, a model was swapped or the runtime under the
loop was replaced. This bench does: each scenario sets up a user with a few
rows of their own (an event, a project, a task), sends the bot one message
and checks the *shape* of the run - which tools were called, what the
database looks like afterwards, how the run stopped - never the wording.

Every scenario runs on each runtime of ``AI_AGENT_RUNTIME`` in turn, through
``build_bot_runner``, with the prompt and the tools a chat reply gets. What
is reported for each run:

- the checks it passed;
- the tools it called, in order, and the rounds that took;
- the model calls, the tokens and the seconds it cost;
- how it stopped.

The answers of a model vary from one run to the next, so the comparison
that means something is the same scenarios before and after a change, more
than once (``--runs``): a check that passes three times out of three on
one side and once on the other is a finding, a single miss is not.

Nothing here runs in CI: it needs a backend and spends tokens. It reads
``AI_API_KEY``, ``AI_BASE_URL`` and ``AI_MODEL`` like the application, and
works in a database of its own, created for the run and dropped after it.

Usage:
    uv run python scripts/agent_bench.py
    uv run python scripts/agent_bench.py --runs 3 --json before.json
    uv run python scripts/agent_bench.py --compare before.json
    uv run python scripts/agent_bench.py --runtime pydantic_ai --scenario confirmation
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")

RUNTIMES = ("native", "pydantic_ai")
EVENT_READS = {"list_upcoming_events", "search_events", "search_everything"}
TASK_READS = {"list_my_tasks", "search_tasks", "search_everything"}

_serial = count(1)


@dataclass
class World:
    """One user talking to one bot, and the rows a scenario gave them."""

    user: object
    bot: object
    bot_profile: object
    conversation: object
    event: object = None
    project: object = None
    task: object = None


def _world(message):
    from django.contrib.auth import get_user_model

    from workspace.ai.models import BotProfile
    from workspace.chat.models import Conversation, ConversationMember, Message

    User = get_user_model()
    serial = next(_serial)
    user = User.objects.create_user(
        username=f"alice{serial}", first_name="Alice", password="bench"
    )
    bot = User.objects.create_user(
        username=f"bot{serial}", first_name="Assistant", password="bench"
    )
    bot_profile = BotProfile.objects.create(
        user=bot, system_prompt="You are a helpful assistant.", is_public=True
    )
    conversation = Conversation.objects.create(
        kind=Conversation.Kind.DM, created_by=user
    )
    ConversationMember.objects.create(conversation=conversation, user=user)
    ConversationMember.objects.create(conversation=conversation, user=bot)
    Message.objects.create(conversation=conversation, author=user, body=message)
    return World(user, bot, bot_profile, conversation)


def _with_dentist(world):
    """An appointment tomorrow at 14:00, on the user's own clock."""
    from django.utils import timezone

    from workspace.calendar.models import Calendar, Event
    from workspace.users.services.settings import get_user_timezone

    tz = get_user_timezone(world.user)
    tomorrow = timezone.localdate(timezone=tz) + timedelta(days=1)
    start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, tzinfo=tz)
    world.event = Event.objects.create(
        calendar=Calendar.objects.create(name="Personal", owner=world.user),
        owner=world.user,
        title="Dentist",
        start=start,
        end=start + timedelta(hours=1),
    )
    return world


def _with_project(world, task=None):
    from workspace.projects.services.projects import create_project
    from workspace.projects.services.tasks import create_task

    world.project = create_project(world.user, name="Home")
    if task:
        world.task = create_task(
            world.project, world.user, title=task, assignees=[world.user]
        )
    return world


def _called(run):
    return [
        call["function"]["name"]
        for round_ in run.tool_data or []
        for call in round_["tool_calls"]
    ]


def _says(run, *words):
    answer = run.response.content.lower()
    return all(word.lower() in answer for word in words)


def _plain_answer():
    return _world("What is 17 times 3? Reply with the number only."), [
        ("calls no tool", lambda run, world: not _called(run)),
        ("answers 51", lambda run, world: _says(run, "51")),
    ]


def _remember():
    from workspace.ai.models import UserMemory

    return _world("My sister is called Elodie. Please remember that."), [
        ("calls save_memory", lambda run, world: "save_memory" in _called(run)),
        (
            "the memory is stored",
            lambda run, world: UserMemory.objects.filter(
                user=world.user, bot=world.bot, content__icontains="lodie"
            ).exists(),
        ),
    ]


def _calendar_read():
    return _with_dentist(_world("What is on my calendar tomorrow?")), [
        ("reads the calendar", lambda run, world: EVENT_READS & set(_called(run))),
        ("names the appointment", lambda run, world: _says(run, "dentist")),
    ]


def _task_create():
    from workspace.projects.models import Task

    world = _with_project(_world('Add a task "Buy milk" to my Home project.'))
    return world, [
        ("calls create_task", lambda run, world: "create_task" in _called(run)),
        (
            "the task exists",
            lambda run, world: Task.objects.filter(
                project=world.project, title__icontains="milk"
            ).exists(),
        ),
    ]


def _two_reads():
    world = _with_project(
        _with_dentist(
            _world(
                "What is on my calendar tomorrow, and which tasks are assigned to me?"
            )
        ),
        task="Renew passport",
    )
    return world, [
        ("reads the calendar", lambda run, world: EVENT_READS & set(_called(run))),
        ("reads the tasks", lambda run, world: TASK_READS & set(_called(run))),
        ("names both", lambda run, world: _says(run, "dentist", "passport")),
    ]


def _confirmation():
    from workspace.ai.harness.runner import StopReason
    from workspace.calendar.models import Event

    world = _with_dentist(_world("Cancel my dentist appointment tomorrow."))
    return world, [
        (
            "the event is still there",
            lambda run, world: Event.objects.filter(
                pk=world.event.pk, is_cancelled=False
            ).exists(),
        ),
        (
            "stops to ask the user",
            lambda run, world: (
                run.stop is StopReason.AWAITING_USER
                and bool(run.context.get("question"))
            ),
        ),
    ]


SCENARIOS = {
    "plain_answer": _plain_answer,
    "remember": _remember,
    "calendar_read": _calendar_read,
    "task_create": _task_create,
    "two_reads": _two_reads,
    "confirmation": _confirmation,
}


def _reply(world):
    """What a chat reply runs, short of posting the message."""
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.ai.services.bot_runs import build_bot_runner
    from workspace.ai.services.conversation_history import build_conversation_history
    from workspace.ai.services.responses import produced_media

    history = build_conversation_history(
        world.conversation.pk, world.bot_profile, world.user
    )
    messages = build_chat_messages(
        world.bot_profile.system_prompt,
        history.messages,
        bot_name=world.bot.get_full_name(),
        user=world.user,
        bot=world.bot,
        summary=history.summary,
    )
    runner = build_bot_runner(
        world.bot_profile, world.user, world.bot, world.conversation.pk
    )
    run = runner.run(messages)
    if not run.response.content and not produced_media(run.context):
        run = runner.retry_final(messages, run)
    return run


def run_scenario(name, runtime):
    from django.test import override_settings

    world, checks = SCENARIOS[name]()
    result = {"scenario": name, "runtime": runtime, "checks": {}}
    started = time.monotonic()
    try:
        with override_settings(AI_AGENT_RUNTIME=runtime):
            run = _reply(world)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["checks"] = dict.fromkeys((label for label, _ in checks), False)
        result["seconds"] = round(time.monotonic() - started, 1)
        return result
    result.update(
        checks={label: bool(check(run, world)) for label, check in checks},
        tools=_called(run),
        rounds=len(run.tool_data or []),
        model_calls=len(run.rounds),
        prompt_tokens=run.usage.prompt_tokens,
        completion_tokens=run.usage.completion_tokens,
        seconds=round(time.monotonic() - started, 1),
        stop=run.stop.value,
        answer=run.response.content,
    )
    return result


def _passed(result):
    return sum(result["checks"].values())


def _print_run(result):
    checks = f"{_passed(result)}/{len(result['checks'])}"
    if "error" in result:
        print(f"  {result['runtime']:<12} {checks:<6} FAILED  {result['error'][:90]}")
        return
    tokens = f"{result['prompt_tokens'] or '?'}+{result['completion_tokens'] or '?'}"
    print(
        f"  {result['runtime']:<12} {checks:<6} {result['stop']:<14}"
        f" {result['model_calls']} calls  {tokens:<12} {result['seconds']:>6}s"
        f"  {', '.join(result['tools']) or '-'}"
    )
    for label, ok in result["checks"].items():
        if not ok:
            print(f"      missed: {label}")


def _print_totals(results):
    totals = defaultdict(lambda: defaultdict(float))
    for result in results:
        total = totals[result["runtime"]]
        total["passed"] += _passed(result)
        total["checks"] += len(result["checks"])
        total["errors"] += "error" in result
        total["model_calls"] += result.get("model_calls", 0)
        total["tokens"] += (result.get("prompt_tokens") or 0) + (
            result.get("completion_tokens") or 0
        )
        total["seconds"] += result["seconds"]
    print("\nTotals")
    for runtime, total in totals.items():
        print(
            f"  {runtime:<12} {int(total['passed'])}/{int(total['checks'])} checks"
            f"  {int(total['errors'])} failed runs  {int(total['model_calls'])} model calls"
            f"  {int(total['tokens'])} tokens  {total['seconds']:.1f}s"
        )


def _print_comparison(results, before):
    def by_cell(rows):
        cells = defaultdict(lambda: [0, 0])
        for row in rows:
            cell = cells[row["scenario"], row["runtime"]]
            cell[0] += _passed(row)
            cell[1] += len(row["checks"])
        return cells

    old, new = by_cell(before["results"]), by_cell(results)
    print(f"\nAgainst {before['model']} ({before['runs']} runs)")
    for key in sorted(new):
        was = "{}/{}".format(*old[key]) if key in old else "-"
        now = "{}/{}".format(*new[key])
        mark = "" if was == now else "   <- changed"
        print(f"  {key[0]:<16} {key[1]:<12} {was:>6} -> {now:<6}{mark}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", action="append", choices=RUNTIMES)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--runs", type=int, default=1, help="runs of each scenario")
    parser.add_argument("--model", help="override AI_MODEL")
    parser.add_argument("--json", type=Path, help="write the results to this file")
    parser.add_argument("--compare", type=Path, help="results of an earlier run")
    args = parser.parse_args()
    # A run takes minutes: each line is shown as it comes, piped or not.
    sys.stdout.reconfigure(line_buffering=True)

    import django

    django.setup()
    # The table is the output: a development setup logs every query.
    logging.disable(logging.INFO)
    from django.conf import settings
    from django.test.utils import setup_databases, teardown_databases

    if args.model:
        settings.AI_MODEL = args.model
    if not settings.AI_API_KEY:
        sys.exit("AI_API_KEY is not set: the bench needs a backend to talk to.")
    runtimes = args.runtime or RUNTIMES
    print(f"Model {settings.AI_MODEL} at {settings.AI_BASE_URL or 'api.openai.com'}")

    databases = setup_databases(verbosity=0, interactive=False)
    results = []
    try:
        for name in args.scenario or SCENARIOS:
            print(f"\n{name}")
            for _ in range(args.runs):
                for runtime in runtimes:
                    result = run_scenario(name, runtime)
                    results.append(result)
                    _print_run(result)
    finally:
        teardown_databases(databases, verbosity=0)

    _print_totals(results)
    if args.compare:
        _print_comparison(results, json.loads(args.compare.read_text(encoding="utf-8")))
    if args.json:
        payload = {"model": settings.AI_MODEL, "runs": args.runs, "results": results}
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWritten to {args.json}")


if __name__ == "__main__":
    main()
