"""AI module: model routing, limits and web search."""

import os

from .env import env_list

AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_BASE_URL = os.getenv("AI_BASE_URL") or None  # For Ollama, LM Studio, etc.
AI_MODEL = os.getenv("AI_MODEL", "gpt-5")
AI_SMALL_MODEL = (
    os.getenv("AI_SMALL_MODEL", "") or None
)  # Fast model for summaries, titles, etc.
AI_EXTRACT_MODEL = os.getenv(
    "AI_EXTRACT_MODEL", ""
)  # Event extraction. Empty = fall back to AI_MODEL.
# Reads a fetched page for what it says about a question: 24k characters per
# chunk, answered as a strict JSON schema, up to eight chunks in flight at
# once. Empty = fall back to AI_SMALL_MODEL, then AI_MODEL.
AI_READING_MODEL = os.getenv("AI_READING_MODEL", "")
# Captions chat image attachments - the only call that requires a multimodal
# model whatever AI_MODEL is. Empty = fall back to AI_MODEL.
AI_VISION_MODEL = os.getenv("AI_VISION_MODEL", "")
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "2048"))
AI_CHAT_CONTEXT_SIZE = int(
    os.getenv("AI_CHAT_CONTEXT_SIZE", "30")
)  # recent messages kept in full; older ones are summarized
AI_VISION_MAX_IMAGES = int(
    os.getenv("AI_VISION_MAX_IMAGES", "8")
)  # max images injected as pixels into a vision bot's history
AI_MAX_TOOL_ROUNDS = int(
    os.getenv("AI_MAX_TOOL_ROUNDS", "20")
)  # max tool-call rounds per bot reply before forcing a final answer
AI_MAX_IDENTICAL_TOOL_CALLS = int(
    os.getenv("AI_MAX_IDENTICAL_TOOL_CALLS", "3")
)  # times the same tool+arguments may run in one reply before being refused
AI_TOOL_CONCURRENCY = int(
    os.getenv("AI_TOOL_CONCURRENCY", "4")
)  # independent tool calls a single round may run at once; 1 runs them one by one
AI_TOOL_RESULT_TASK_MAX_CHARS = int(
    os.getenv("AI_TOOL_RESULT_TASK_MAX_CHARS", "2000")
)  # tool result kept in AITask.raw_messages, a debug record read by a human
AI_TOOL_RESULT_STORE_MAX_CHARS = int(
    os.getenv("AI_TOOL_RESULT_STORE_MAX_CHARS", "12000")
)  # tool result kept in Message.tool_data and replayed on the next turn;
# matches the web fetch budget so a fetched page survives whole
AI_TOOL_RESULT_REPLAY_MIN_CHARS = int(
    os.getenv("AI_TOOL_RESULT_REPLAY_MIN_CHARS", "1500")
)  # floor a replayed tool result decays to once its turn is old
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

# Text-to-speech for bot voice messages. A voice is a free-text instruction or
# a reference recording to clone, never an OpenAI `voice` id, so AI_TTS_BASE_URL
# must point at a route that forwards the request body untouched: an
# OpenAI-standard proxy drops every non-OpenAI field, which silently reduces
# every bot to the same voice - or, on a clone-based model, errors outright.
# Pointing straight at the speech server is the simplest way to get that, and it
# needs no key of its own - AI_API_KEY is sent only when set.
AI_TTS_MODEL = os.getenv("AI_TTS_MODEL", "")
AI_TTS_BASE_URL = os.getenv("AI_TTS_BASE_URL") or None
# What the speech model can be told it is reading, spelled out in English and
# lowercase. The default is what qwen3-voicedesign accepts, measured against
# it; another model takes another list, and naming one it does not know is a
# hard error, so this is the list a deployment has to keep true. A language
# outside it is not refused - it is simply not announced, and the model
# detects it from the text.
AI_TTS_LANGUAGES = env_list("AI_TTS_LANGUAGES") or [
    "french",
    "english",
    "spanish",
    "german",
    "italian",
    "portuguese",
    "russian",
    "japanese",
    "korean",
    "chinese",
]
AI_TTS_VOICE = os.getenv(
    "AI_TTS_VOICE",
    "A warm, natural adult voice with a calm, friendly delivery.",
)  # voice of a bot whose profile neither describes nor records one
# Recording a bot with no reference of its own is cloned from, and the exact
# words it says. A clone-based speech model has no default voice and ignores
# AI_TTS_VOICE, so leaving these empty gives such a bot a new speaker on
# every message; a deployment on one of those models wants a file here.
AI_TTS_VOICE_REF = os.getenv("AI_TTS_VOICE_REF", "")
AI_TTS_VOICE_REF_TEXT = os.getenv("AI_TTS_VOICE_REF_TEXT", "")
AI_TTS_MAX_CHARS = int(
    os.getenv("AI_TTS_MAX_CHARS", "700")
)  # characters per voice message; generation takes about as long as the audio
AI_TTS_TIMEOUT = int(os.getenv("AI_TTS_TIMEOUT", "180"))  # seconds per synthesis call
AI_TTS_MAX_ATTEMPTS = int(
    os.getenv("AI_TTS_MAX_ATTEMPTS", "3")
)  # calls per voice message before giving up (1 = no retry)
AI_TTS_RETRY_DELAY = float(
    os.getenv("AI_TTS_RETRY_DELAY", "2")
)  # seconds before the first retry, doubled after each attempt
