# MCHS AI Admin Bot V3

Telegram-бот для AI-анализа, модерации и подготовки ответов на комментарии канала.

## Run & Operate

- `python -m app.main` — запуск Telegram polling и healthcheck на порту 8080
- `python -m pytest` — запуск тестов
- Healthcheck: `GET /health`
- Required secrets: `BOT_TOKEN`, `ADMIN_IDS`, and either `OPENROUTER_API_KEY` or `OPENAI_API_KEY`

## Stack

- Python 3.12
- Telegram bot: aiogram 3
- AI: OpenAI-compatible API via OpenRouter or OpenAI
- DB: SQLAlchemy AsyncIO; uses the configured PostgreSQL `DATABASE_URL` when available and SQLite for local fallback
- Healthcheck: FastAPI + Uvicorn

## Where things live

- `app/main.py` — application entry point and `/health` endpoint
- `app/config.py` — environment-backed settings
- `app/bot/` — Telegram handlers and keyboards
- `app/services/openai_service.py` — AI analysis and response generation
- `app/database/` — SQLAlchemy models and repositories

## Architecture decisions

- OpenRouter is the default AI transport when `OPENROUTER_API_KEY` is present; `OPENAI_API_KEY` remains supported.
- PostgreSQL is used through the runtime `DATABASE_URL` when configured; local development falls back to `data/bot.db`.
- Comment records retain source-post context, AI-processing state, and the published reply to prevent duplicate automatic responses.

## Product

- Admin panel for comments, statistics, AI mode, moderation, memory, and settings.
- Manual and confidence-gated automatic reply modes.
- Persistent knowledge commands: `/remember`, `/knowledge`, `/forget`.

## Gotchas

- Each Telegram administrator must start the bot with `/start` before receiving notifications.
- `ADMIN_IDS` is a comma-separated list of Telegram numeric IDs.
