# Agent evals

`scripts/agent_evals.py` runs a bot through fixed scenarios on a real model
and checks the shape of each run: which tools were called, what the database
holds afterwards, how the run stopped. Never the wording of the answer.

Unit tests say that the loop stops where it should. They cannot say that a bot
still picks the right tool after the system prompt was rewritten, the model
swapped, or `pydantic-ai-slim` bumped. Run the evals before and after such a
change, and compare.

```bash
uv run python scripts/agent_evals.py --runs 3 --json before.json
# ... change something ...
uv run python scripts/agent_evals.py --runs 3 --compare before.json
uv run python scripts/agent_evals.py --model gpt-5 --scenario confirmation
```

It talks to the backend the application is configured for (`AI_API_KEY`,
`AI_BASE_URL`, `AI_MODEL`; `--model` overrides the last), so it spends tokens
and is not part of CI. It works in a database of its own, created for the run
and dropped after it: the development database is never touched.

## What it runs

Each scenario creates a user, a bot and the few rows the question is about,
posts one message, and runs what a chat reply runs short of posting the
answer: the real history, the real system prompt, every registered tool,
through `run_bot`. Nothing is simulated, so a change to the prompt, to a
tool's description or to the loop shows up in the result.

| Scenario | The user says | What is checked |
|---|---|---|
| `plain_answer` | What is 17 times 3? | No tool is called, the answer says 51. |
| `remember` | My sister is called Elodie, remember that. | `save_memory` is called, the memory is stored. |
| `calendar_read` | What is on my calendar tomorrow? | The calendar is read, the answer names the appointment. |
| `task_create` | Add a task "Buy milk" to my Home project. | `create_task` is called, the task exists. |
| `two_reads` | What is on my calendar tomorrow, and which tasks are assigned to me? | Calendar and tasks are both read, the answer names both. |
| `confirmation` | Cancel my dentist appointment tomorrow. | The event is still there, the run stopped to ask the user. |

Web search, images and voice are left out: they need more than a chat model
to answer.

## Reading the output

One line per run: the checks passed, how the run stopped, the model calls it
made, its tokens (prompt + completion), the seconds it took and the tools it
called, in order. A missed check is named under its run. The total adds
every run up.

A model does not answer twice the same way, so one miss proves little.
`--runs 3` runs each scenario three times, and `--compare` then sets the
checks passed per scenario beside those of the earlier file: three out of
three on one side and one on the other is a finding.

That is how the evals earned their place. While the bots were being moved to
Pydantic AI, `two_reads` went from six checks out of six to two: the library
sent each reply's reasoning back to the model, and `gpt-oss` then wrote its
next tool call into its reasoning, where nothing runs it. No unit test could
have seen it.

## Adding a scenario

A scenario is a function in `SCENARIOS` returning the world it built and its
checks, each a label and a function of the run and that world. Check what
must hold whatever the model writes: a tool in `run.tool_data`, a row in the
database, `run.stop`. A check on the answer's text stays to a word the answer
cannot do without.
