# AI Assistants

Configurable chat bots backed by any OpenAI-compatible provider, with tool calling, vision, memory, and scheduled messages.

## Features

- **Bot profiles** - Each bot is a real user with its own avatar, system prompt, model override, and description. Bots appear in chat like any other member.
- **Bring your own model** - Works with the OpenAI API or any compatible endpoint (Ollama, LM Studio, vLLM, ...). Per-bot model override falls back to the global `AI_MODEL`.
- **Vision** - Bots with vision enabled can read images attached to messages.
- **Function calling (tools)** - Bots can look up information and act on your behalf (see [Tools](#tools) below). Enable/disable per bot.
- **Extended thinking** - Reasoning models are supported for step-by-step problem solving.
- **Persistent memory** - Bots remember facts about you across conversations (name, preferences, projects) and can recall or forget them on request.
- **Conversation summaries** - Long conversations are rolled up into a running summary so bots keep context without resending the whole history.
- **Image generation & editing** - Generate or edit images inline via a configurable image model (`AI_IMAGE_MODEL`).
- **Scheduled messages** - Bots can send one-time or recurring messages (reminders, digests) driven by Celery beat.
- **AI features across modules** - Email summarization and reply suggestions (Mail), calendar event extraction, and more.

## Access control

Bots are private by default. A bot is accessible to a user when it is public, was created by that user, or the user is in the bot's allowed users/groups (superusers see all active bots). Access is centralized on `BotProfile.accessible_by(user)` / `is_accessible_by(user)` - reuse those helpers rather than re-checking flags.

## Tools

When tool calling is enabled, bots can invoke:

| Tool | What it does |
|------|--------------|
| `get_current_user_info` | Read the current user's profile (name, email, join date) |
| `get_my_avatar` | Inspect the bot's own avatar |
| `save_memory` / `delete_memory` | Store or forget a persistent fact about the user |
| `web_search` / `read_webpage` | Search the web (requires a SearXNG instance) and read a page |
| `get_weather` | Current weather by place name (Open-Meteo, keyless) |
| `generate_image` / `edit_image` | Create or edit images |
| `schedule_message` / `cancel_schedule` / `list_schedules` | Manage bot-initiated scheduled messages |

## Configuration

AI is disabled until `AI_API_KEY` is set. All variables are optional beyond that - see [`.env.example`](../../.env.example) for the full list and defaults.

| Variable | Purpose |
|---|---|
| `AI_API_KEY` | Provider API key. Without it, AI features are disabled. |
| `AI_BASE_URL` | Custom endpoint for OpenAI-compatible providers (Ollama, LM Studio, ...). |
| `AI_MODEL` | Default chat model, used when a bot has no model override. |
| `AI_SMALL_MODEL` | Fast model for summaries and titles. |
| `AI_EXTRACT_MODEL` | Model for mail event extraction (defaults to `AI_MODEL`). |
| `AI_MAX_TOKENS` | Maximum tokens per response. |
| `AI_TIMEOUT` | Seconds per AI request. |
| `AI_IMAGE_MODEL` / `AI_IMAGE_BASE_URL` | Image generation model and optional separate endpoint. |
| `SEARXNG_URL` | SearXNG instance used by the web search tool. |

Scheduled messages and conversation summaries run as background work, so **Celery worker and beat should be running in production**.

## API

All endpoints under `/api/v1/ai/` - see the [Swagger UI](/schema/swagger-ui/) for full documentation.
