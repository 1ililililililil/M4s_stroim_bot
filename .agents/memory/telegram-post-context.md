---
name: Telegram post context
description: Context extraction for Telegram comments and nested replies
---

When Telegram supplies a nested `reply_to_message`, treat the direct reply as the parent comment and its nested target as the original post; otherwise the direct reply is the available post context.

**Why:** Comment questions can contain pronouns that require the publication context, while replies to another comment need both the original post and the parent comment.

**How to apply:** Keep post context bounded, preserve both Telegram message IDs, and continue gracefully when the update has no replied-to message or no text/caption.