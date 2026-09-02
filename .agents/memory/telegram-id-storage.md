---
name: Telegram ID storage
description: PostgreSQL sizing guidance for Telegram chat and user identifiers.
---

Telegram supergroup and user identifiers can exceed PostgreSQL `INTEGER`; store raw Telegram IDs and message/chat reference IDs as `BIGINT`.

**Why:** A live discussion-group update used a negative chat ID outside the 32-bit range and failed before comment processing.

**How to apply:** Use 64-bit columns for Telegram identifiers and verify existing PostgreSQL schemas during startup or migration before processing updates.