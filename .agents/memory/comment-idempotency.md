---
name: Comment idempotency
description: Durable duplicate protection for Telegram comment processing
---

Duplicate protection for Telegram comments must be database-backed: identify messages by `(chat_id, telegram_message_id)`, use an atomic conditional claim for processing, and claim the automatic reply before sending it.

**Why:** Telegram updates and queue entries can be delivered more than once, and workers can race; in-memory sets disappear on workflow restarts and cannot prevent concurrent sends.

**How to apply:** Preserve the unique message constraint and conditional database updates whenever comment processing or queue behavior is changed.