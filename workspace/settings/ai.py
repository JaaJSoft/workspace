"""AI module: model routing, limits and web search."""

import os

AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_BASE_URL = os.getenv("AI_BASE_URL") or None  # For Ollama, LM Studio, etc.
AI_MODEL = os.getenv("AI_MODEL", "gpt-5")
AI_SMALL_MODEL = (
    os.getenv("AI_SMALL_MODEL", "") or None
)  # Fast model for summaries, titles, etc.
AI_EXTRACT_MODEL = os.getenv(
    "AI_EXTRACT_MODEL", ""
)  # Event extraction. Empty = fall back to AI_MODEL.
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "2048"))
AI_CHAT_CONTEXT_SIZE = int(
    os.getenv("AI_CHAT_CONTEXT_SIZE", "30")
)  # recent messages kept in full; older ones are summarized
AI_VISION_MAX_IMAGES = int(
    os.getenv("AI_VISION_MAX_IMAGES", "8")
)  # max images injected as pixels into a vision bot's history
AI_MAX_TOOL_ROUNDS = int(
    os.getenv("AI_MAX_TOOL_ROUNDS", "10")
)  # max tool-call rounds per bot reply before forcing a final answer
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "300"))  # seconds per request
AI_MAX_RETRIES = int(
    os.getenv("AI_MAX_RETRIES", "2")
)  # retries on transient errors (timeout, 5xx)
AI_TASK_RETENTION_DAYS = int(os.getenv("AI_TASK_RETENTION_DAYS", "90"))
AI_IMAGE_MODEL = os.getenv("AI_IMAGE_MODEL", "")
AI_IMAGE_BASE_URL = os.getenv("AI_IMAGE_BASE_URL") or None
AI_IMAGE_MAX_ATTEMPTS = int(
    os.getenv("AI_IMAGE_MAX_ATTEMPTS", "3")
)  # calls per image before giving up (1 = no retry)
AI_IMAGE_RETRY_DELAY = float(
    os.getenv("AI_IMAGE_RETRY_DELAY", "2")
)  # seconds before the first image retry, doubled after each attempt
AI_IMAGE_FAILURE_BUDGET = int(
    os.getenv("AI_IMAGE_FAILURE_BUDGET", "10")
)  # failed image tool calls a bot reply may burn before it must stop retrying
SEARXNG_URL = os.getenv("SEARXNG_URL", "")  # e.g. http://searxng:8080
SEARXNG_BLOCKED_DOMAINS = os.getenv(
    "SEARXNG_BLOCKED_DOMAINS", ""
)  # comma-separated, e.g. "evil.com,spam.org"
