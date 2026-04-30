"""Prometheus metrics for the AI module.

All metric names in this file MUST start with "ai_".
"""

from prometheus_client import Counter, Histogram

_P = 'ai'

AI_REQUEST_DURATION = Histogram(
    f'{_P}_request_duration_seconds',
    'Wall-clock time of one chat.completions.create() call, by model and status',
    ['model', 'status'],
)

AI_TOKENS = Counter(
    f'{_P}_tokens_total',
    'Tokens reported by the LLM API, by model and kind (prompt/completion)',
    ['model', 'kind'],
)

AI_IMAGE_REQUESTS = Counter(
    f'{_P}_image_requests_total',
    'Image generation requests issued, by model and status (ok/error)',
    ['model', 'status'],
)
