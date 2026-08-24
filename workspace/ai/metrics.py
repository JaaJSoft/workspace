"""Prometheus metrics for the AI module.

All metric names in this file MUST start with "ai_".
"""

from workspace.common.metrics import safe_counter, safe_histogram

_P = "ai"

AI_REQUEST_DURATION = safe_histogram(
    f"{_P}_request_duration_seconds",
    "Wall-clock time of one chat.completions.create() call, by model and status",
    ["model", "status"],
)

AI_TOKENS = safe_counter(
    f"{_P}_tokens_total",
    "Tokens reported by the LLM API, by model and kind (prompt/completion)",
    ["model", "kind"],
)

AI_IMAGE_REQUESTS = safe_counter(
    f"{_P}_image_requests_total",
    "Image requests issued, by model, op (generate/edit) and status (ok/error)",
    ["model", "op", "status"],
)

AI_AGENT_CHECKINS = safe_counter(
    f"{_P}_agent_checkins_total",
    "Agent goal check-ins run, by outcome "
    "(message/silent/suppressed/error) — watches the talkativeness ratio",
    ["outcome"],
)

AI_TOOL_CALLS = safe_counter(
    f"{_P}_tool_calls_total",
    "Tool calls issued while composing a reply, by tool and status "
    "(ok/error/repeat) — 'repeat' is a duplicate call the loop refused to run",
    ["tool", "status"],
)

AI_TOOL_ROUNDS = safe_histogram(
    f"{_P}_tool_rounds",
    "Rounds of tool calls spent on one reply, by model — 0 when the model "
    "answered without calling anything",
    ["model"],
    buckets=(0, 1, 2, 3, 4, 5, 8, 12, 20, float("inf")),
)

AI_TOOL_LOOP_STOPS = safe_counter(
    f"{_P}_tool_loop_stops_total",
    "Tool loops cut short instead of ending on the model's own answer, "
    "by reason (round_cap/repeat_loop)",
    ["reason"],
)

AI_HISTORY_TOOL_CHARS = safe_histogram(
    f"{_P}_history_tool_chars",
    "Characters of past tool results replayed into one rebuilt conversation "
    "history - what the recency-weighted replay budget actually costs",
    buckets=(0, 500, 2_000, 8_000, 20_000, 50_000, 120_000, float("inf")),
)
