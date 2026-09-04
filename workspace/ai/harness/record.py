"""The two persisted accounts of a run, kept in step with each other.

``rounds`` is the debug record a human reads on ``AITask.raw_messages``:
every reply and every tool execution, results cut short. ``tool_data`` is
the replayable history kept on ``Message.tool_data``, which the next turn
rebuilds the ``assistant(tool_calls) -> tool(result)`` sequence from, so
its results keep far more.
"""

from workspace.ai.services.llm import truncate_tool_result


class RunRecord:
    def __init__(self, toolset, *, task_max_chars, store_max_chars):
        self._toolset = toolset
        self._task_max_chars = task_max_chars
        self._store_max_chars = store_max_chars
        self.rounds = []
        self.tool_data = []

    def assistant_turn(self, response):
        """Open a round on a reply that asked for tools."""
        self.rounds.append({"response": response.as_record(), "tool_executions": []})
        self.tool_data.append(
            {
                "assistant_content": response.raw_content,
                "thinking": response.thinking,
                "tool_calls": [tc.as_message_part() for tc in response.tool_calls],
                "results": [],
            }
        )

    def tool_result(self, outcome, tool_content):
        """Add what one call of the open round came back with.

        *tool_content* is the result as the model reads it, which for an
        image is a list of parts: only its text is stored for replay.
        """
        call = outcome.call
        # Rides inside the residue of a trimmed result, so a stub the model
        # reads later still says which call produced it.
        hint = self._toolset.describe_call(call.name, call.arguments)
        self.rounds[-1]["tool_executions"].append(
            {
                "tool_call_id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "result": truncate_tool_result(
                    outcome.result, self._task_max_chars, hint=hint
                ),
            }
        )
        text = outcome.result
        if isinstance(tool_content, list):
            text = next(
                (
                    part["text"]
                    for part in tool_content
                    if isinstance(part, dict) and part.get("type") == "text"
                ),
                outcome.result,
            )
        self.tool_data[-1]["results"].append(
            {
                "tool_call_id": call.id,
                "content": truncate_tool_result(text, self._store_max_chars, hint=hint),
            }
        )

    def reply(self, response, **flags):
        """Add a reply that opened no round: the answer, or a call never run."""
        self.rounds.append({"response": response.as_record(), **flags})

    def flag(self, **flags):
        """Mark the latest round with how it ended."""
        self.rounds[-1].update(flags)
