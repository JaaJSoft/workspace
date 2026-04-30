"""Prometheus metrics for the AI module.

All metric names in this file MUST start with "ai_".
"""

from workspace.common.metrics import safe_counter, safe_histogram

_P = 'ai'

AI_REQUEST_DURATION = safe_histogram(
    f'{_P}_request_duration_seconds',
    'Wall-clock time of one chat.completions.create() call, by model and status',
    ['model', 'status'],
)

AI_TOKENS = safe_counter(
    f'{_P}_tokens_total',
    'Tokens reported by the LLM API, by model and kind (prompt/completion)',
    ['model', 'kind'],
)

AI_IMAGE_REQUESTS = safe_counter(
    f'{_P}_image_requests_total',
    'Image requests issued, by model, op (generate/edit) and status (ok/error)',
    ['model', 'op', 'status'],
)
