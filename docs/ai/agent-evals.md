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
uv run python scripts/agent_evals.py --model gpt-5 --case confirmation
```

The scenarios are a [Pydantic Evals](https://pydantic.dev/docs/ai/evals/)
dataset: the cases, the checks and the report are the library's, the script
only says what a case sets up and what a check reads.

It talks to the backend the application is configured for (`AI_API_KEY`,
`AI_BASE_URL`, `AI_MODEL`; `--model` overrides the last), so it spends tokens
and is not part of CI. It works in a database of its own, created for the run
and dropped after it: the development database is never touched.

## What it runs

Each case creates a user, a bot and the few rows the question is about,
posts one message, and runs what a chat reply runs short of posting the
answer: the real history, the real system prompt, every registered tool,
through `run_bot`. Nothing is simulated, so a change to the prompt, to a
tool's description or to the loop shows up in the result.

| Case | The user says | What is checked |
|---|---|---|
| `plain_answer` | What is 17 times 3? | No tool is called, the answer says 51. |
| `remember` | My sister is called Elodie, remember that. | `save_memory` is called, the memory is stored. |
| `calendar_read` | What is on my calendar tomorrow? | The calendar is read, the answer names the appointment. |
| `task_create` | Add a task "Buy milk" to my Home project. | `create_task` is called, the task exists. |
| `two_reads` | What is on my calendar tomorrow, and which tasks are assigned to me? | Calendar and tasks are both read, the answer names the appointment and the task. |
| `language` | Salut, je dois faire quoi aujourd'hui ? (from a user whose interface is in English) | The tasks are read, the answer is in French. |
| `confirmation` | Cancel my dentist appointment tomorrow. | The event is still there, the run stopped to ask the user. |

Web search, images and voice are left out: they need more than a chat model
to answer.

## Reading the output

One row per run of a case:

- **Labels**: the tools it called, in order, and how the run stopped.
- **Metrics**: the model calls it made and its tokens.
- **Assertions**: one mark per check of the case, in the order the case lists
  them. The names are in the JSON report.
- **Duration**, and a last row of averages.

A model does not answer twice the same way, so one miss proves little.
`--runs 3` runs each case three times, and `--compare` then prints the
report as a diff against an earlier one, row by row: run it with the same
`--runs` as the file it is compared with, or the rows do not line up. Three
marks out of three on one side and one on the other is a finding.

That is how the evals earned their place. While the bots were being moved to
Pydantic AI, `two_reads` went from every check passing to one in three: the
library sent each reply's reasoning back to the model, and `gpt-oss` then
wrote its next tool call into its reasoning, where nothing runs it. No unit
test could have seen it.

## Adding a case

A case is a `Case` in `CASES`: a `Scenario` (the message, and the rows the user
owns beforehand) and its checks. A check is a function of the run's `Outcome`
returning a boolean, and its name is the one it has in the report. Check what
must hold whatever the model writes: a tool that was called, a row in the
database, how the run stopped. A check on the answer's text stays to a word
the answer cannot do without. A fact about the database is read in
`reply_to`, where the run happened, and carried on the `Outcome`.
