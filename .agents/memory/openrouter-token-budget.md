---
name: OpenRouter token budget
description: OpenRouter credit-limit behavior when using the OpenAI-compatible Chat Completions client
---

The OpenRouter-compatible client must send an explicit `max_tokens=8192` for this bot's Chat Completions requests. Omitting the limit can make OpenRouter evaluate the request against a 65536-token maximum and return HTTP 402 even when a smaller request is affordable.

**Why:** The configured OpenRouter account successfully handled the same model and prompt with an 8192-token cap, while the uncapped request exceeded the remaining credit allowance.

**How to apply:** Keep the cap in the shared AI request helper so analysis, combined analysis/reply, and manual response generation all inherit it.