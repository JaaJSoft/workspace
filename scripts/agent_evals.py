#!/usr/bin/env python
"""Evaluate a bot on fixed scenarios, against a real model.

A unit test can say that the loop stops where it should. It cannot say
that a bot still picks the right tool, or still stops for a confirmation,
after a prompt was rewritten, a model was swapped or the agent library was
bumped. These evals do: each case sets up a user with a few rows of their
own (an event, a project, a task), sends the bot one message and checks the
*shape* of the run - which tools were called, what the database looks like
afterwards, how the run stopped - never the wording.

The cases are a Pydantic Evals dataset. Each one runs through ``run_bot``,
with the prompt and the tools a chat reply gets, and its report shows per
case the checks it passed, the tools it called, the model calls and the
tokens it cost, and how it stopped.

The answers of a model vary from one run to the next, so the comparison
that means something is the same cases before and after a change, more
than once (``--runs``): a check that passes three times out of three on
one side and once on the other is a finding, a single miss is not.

Nothing here runs in CI: it needs a backend and spends tokens. It reads
``AI_API_KEY``, ``AI_BASE_URL`` and ``AI_MODEL`` like the application, and
works in a database of its own, created for the run and dropped after it.

Usage:
    uv run python scripts/agent_evals.py
    uv run python scripts/agent_evals.py --runs 3 --json before.json
    uv run python scripts/agent_evals.py --runs 3 --compare before.json
    uv run python scripts/agent_evals.py --model gpt-5 --case confirmation
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

from pydantic_evals import Case, Dataset, increment_eval_metric
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.reporting import EvaluationReportAdapter

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")

EVENT_READS = {"list_upcoming_events", "search_events", "search_everything"}
TASK_READS = {"list_my_tasks", "search_tasks", "search_everything"}
FRENCH_WORDS = frozenset(
    "le la les un une des du de et est sont vous tu votre vos ton tes pour dans "
    "avec sur que qui pas aujourd'hui tâche tâches voici".split()
)
ENGLISH_WORDS = frozenset(
    "the a an of and is are you your for in with on that which not today task "
    "tasks here have has".split()
)

_serial = count(1)


@dataclass
class Scenario:
    """What a case sends the bot, and the rows its user owns beforehand."""

    message: str
    # An appointment at the dentist, tomorrow at 14:00 on the user's own clock.
    dentist: bool = False
    # A project called Home, and a task assigned to the user in it.
    project: bool = False
    task: str = ""
    # The language of the user's interface, when the case turns on it.
    interface_language: str = ""


@dataclass
class Outcome:
    """What a run came to, as plain facts the checks read."""

    answer: str
    tools: list[str]
    stop: str
    # What the database and the run hold afterwards.
    memory_stored: bool = False
    task_created: bool = False
    event_kept: bool = True
    asked_user: bool = False


def _world(scenario):
    """One user talking to one bot, with the rows the scenario gives them."""
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from workspace.ai.models import BotProfile
    from workspace.calendar.models import Calendar, Event
    from workspace.chat.models import Conversation, ConversationMember, Message
    from workspace.projects.services.projects import create_project
    from workspace.projects.services.tasks import create_task
    from workspace.users.services.settings import get_user_timezone, set_setting

    User = get_user_model()
    serial = next(_serial)
    user = User.objects.create_user(
        username=f"alice{serial}", first_name="Alice", password="evals"
    )
    bot = User.objects.create_user(
        username=f"bot{serial}", first_name="Assistant", password="evals"
    )
    if scenario.interface_language:
        set_setting(user, "core", "language", scenario.interface_language)
    world = {
        "user": user,
        "bot": bot,
        "bot_profile": BotProfile.objects.create(
            user=bot, system_prompt="You are a helpful assistant.", is_public=True
        ),
        "conversation": Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=user
        ),
    }
    ConversationMember.objects.create(conversation=world["conversation"], user=user)
    ConversationMember.objects.create(conversation=world["conversation"], user=bot)
    Message.objects.create(
        conversation=world["conversation"], author=user, body=scenario.message
    )
    if scenario.dentist:
        tz = get_user_timezone(user)
        tomorrow = timezone.localdate(timezone=tz) + timedelta(days=1)
        start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, tzinfo=tz)
        world["event"] = Event.objects.create(
            calendar=Calendar.objects.create(name="Personal", owner=user),
            owner=user,
            title="Dentist",
            start=start,
            end=start + timedelta(hours=1),
        )
    if scenario.project:
        world["project"] = create_project(user, name="Home")
        if scenario.task:
            create_task(world["project"], user, title=scenario.task, assignees=[user])
    return world


def reply_to(scenario: Scenario) -> Outcome:
    """What a chat reply runs, short of posting the message."""
    from django.db import connections
    from pydantic_ai.messages import ModelResponse

    from workspace.ai.models import UserMemory
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.ai.services.bot_runs import run_bot
    from workspace.ai.services.conversation_history import build_conversation_history
    from workspace.calendar.models import Event
    from workspace.projects.models import Task

    try:
        world = _world(scenario)
        user, bot, conversation = world["user"], world["bot"], world["conversation"]
        history = build_conversation_history(
            conversation.pk, world["bot_profile"], user
        )
        messages = build_chat_messages(
            world["bot_profile"].system_prompt,
            history.messages,
            bot_name=bot.get_full_name(),
            user=user,
            bot=bot,
            summary=history.summary,
        )
        run = run_bot(world["bot_profile"], user, bot, conversation.pk, messages)

        tools = [
            call["function"]["name"]
            for round_ in run.tool_data or []
            for call in round_["tool_calls"]
        ]
        increment_eval_metric(
            "model_calls", sum(isinstance(m, ModelResponse) for m in run.new_messages)
        )
        increment_eval_metric("input_tokens", run.usage.input_tokens)
        increment_eval_metric("output_tokens", run.usage.output_tokens)
        return Outcome(
            answer=run.output,
            tools=tools,
            stop=run.stop.value,
            memory_stored=UserMemory.objects.filter(user=user, bot=bot).exists(),
            task_created=Task.objects.filter(
                project=world.get("project"), title__icontains="milk"
            ).exists(),
            event_kept="event" not in world
            or Event.objects.filter(pk=world["event"].pk, is_cancelled=False).exists(),
            asked_user=bool(run.context.get("question")),
        )
    finally:
        # The task runs on a thread of the evaluation, with a connection of its own.
        connections.close_all()


# Read on every case: a label in the report, set beside the baseline's.


def tools(ctx: EvaluatorContext[Scenario, Outcome]) -> str:
    return ", ".join(ctx.output.tools) or "-"


def stop(ctx: EvaluatorContext[Scenario, Outcome]) -> str:
    return ctx.output.stop


# The checks: each one is a function of what the run came to, and its name
# is the one it has in the report.


def calls_no_tool(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return not ctx.output.tools


def answers_51(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return "51" in ctx.output.answer


def calls_save_memory(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return "save_memory" in ctx.output.tools


def the_memory_is_stored(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return ctx.output.memory_stored


def reads_the_calendar(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return bool(EVENT_READS & set(ctx.output.tools))


def reads_the_tasks(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return bool(TASK_READS & set(ctx.output.tools))


def names_the_appointment(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return "dentist" in ctx.output.answer.lower()


def names_the_task(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return "passport" in ctx.output.answer.lower()


def calls_create_task(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return "create_task" in ctx.output.tools


def the_task_exists(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return ctx.output.task_created


def the_event_is_still_there(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return ctx.output.event_kept


def stops_to_ask_the_user(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    return ctx.output.stop == "awaiting_user" and ctx.output.asked_user


def answers_in_french(ctx: EvaluatorContext[Scenario, Outcome]) -> bool:
    """Whether the answer reads as French: more of its small words than of English's."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüœ']+", ctx.output.answer.lower())
    return sum(w in FRENCH_WORDS for w in words) > sum(
        w in ENGLISH_WORDS for w in words
    )


CASES = [
    Case(
        name="plain_answer",
        inputs=Scenario("What is 17 times 3? Reply with the number only."),
        evaluators=(calls_no_tool, answers_51),
    ),
    Case(
        name="remember",
        inputs=Scenario("My sister is called Elodie. Please remember that."),
        evaluators=(calls_save_memory, the_memory_is_stored),
    ),
    Case(
        name="calendar_read",
        inputs=Scenario("What is on my calendar tomorrow?", dentist=True),
        evaluators=(reads_the_calendar, names_the_appointment),
    ),
    Case(
        name="task_create",
        inputs=Scenario('Add a task "Buy milk" to my Home project.', project=True),
        evaluators=(calls_create_task, the_task_exists),
    ),
    Case(
        name="two_reads",
        inputs=Scenario(
            "What is on my calendar tomorrow, and which tasks are assigned to me?",
            dentist=True,
            project=True,
            task="Renew passport",
        ),
        evaluators=(
            reads_the_calendar,
            reads_the_tasks,
            names_the_appointment,
            names_the_task,
        ),
    ),
    Case(
        name="language",
        inputs=Scenario(
            "Salut, je dois faire quoi aujourd'hui ?",
            project=True,
            task="Renew passport",
            interface_language="en",
        ),
        evaluators=(reads_the_tasks, answers_in_french),
    ),
    Case(
        name="confirmation",
        inputs=Scenario("Cancel my dentist appointment tomorrow.", dentist=True),
        evaluators=(the_event_is_still_there, stops_to_ask_the_user),
    ),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--case", action="append", choices=[case.name for case in CASES]
    )
    parser.add_argument("--runs", type=int, default=1, help="runs of each case")
    parser.add_argument("--model", help="override AI_MODEL")
    parser.add_argument("--json", type=Path, help="write the report to this file")
    parser.add_argument("--compare", type=Path, help="report of an earlier run")
    args = parser.parse_args()

    import django

    django.setup()
    # The report is the output: a development setup logs every query.
    logging.disable(logging.INFO)
    from django.conf import settings
    from django.test.utils import setup_databases, teardown_databases

    if args.model:
        settings.AI_MODEL = args.model
    if not settings.AI_API_KEY:
        sys.exit("AI_API_KEY is not set: the evals need a backend to talk to.")

    cases = [case for case in CASES if not args.case or case.name in args.case]
    dataset = Dataset(name="agent", cases=cases, evaluators=(tools, stop))
    databases = setup_databases(verbosity=0, interactive=False)
    try:
        report = dataset.evaluate_sync(
            reply_to,
            name=settings.AI_MODEL,
            repeat=args.runs,
            # One reply at a time: the backend is the same for every case.
            max_concurrency=1,
            metadata={
                "model": settings.AI_MODEL,
                "backend": settings.AI_BASE_URL or "api.openai.com",
            },
        )
    finally:
        teardown_databases(databases, verbosity=0)

    baseline = None
    if args.compare:
        baseline = EvaluationReportAdapter.validate_json(args.compare.read_bytes())
    report.print(baseline=baseline, include_reasons=False)
    if args.json:
        args.json.write_bytes(EvaluationReportAdapter.dump_json(report, indent=2))
        print(f"Written to {args.json}")


if __name__ == "__main__":
    main()
