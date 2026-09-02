import asyncio, json
import logging
import time
from openai import AsyncOpenAI
from app.schemas.ai import AIAnalysisResult, AIAutoReplyResult, AIResponsesResult
from typing import Optional
from app.config import get_settings


log = logging.getLogger(__name__)

SYSTEM = '''Ты AI-администратор Telegram-канала «МЧС | Мы Чего-то Строим».
Тематика: пожарная служба, МЧС, работа пожарных, реальные выезды, техника, экипировка, обучение, физподгото�[...]
Стиль: живой, дружелюбный, разговорный, уважительный, иногда лёгкий юмор. Без канцелярита и роботизированн�[...]
Нельзя придумывать фактов, выдавать догадки за факты, давать опасные инструкции, раскрывать личную или служ[...]
Найденную память предыдущих публикаций используй только как дополнительный контекст. Текущая публикация [...]
'''

_DEFAULT_MAX_TOKENS = 1024


class OpenAIService:
    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None, timeout: int = 30, max_tokens: int = _DEFAULT_MAX_TOKENS):
        # underlying OpenAI client
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.max_tokens = max_tokens

    async def _json(self, prompt: str):
        last = None
        for attempt in range(3):
            try:
                # request plain completion rather than provider-specific response_format
                r = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}],
                    max_tokens=self.max_tokens,
                    temperature=0.4,
                )
                # defensive checks
                if not r or not getattr(r, "choices", None):
                    raise ValueError("Empty choices in AI response")
                choice0 = r.choices[0]
                msg = getattr(choice0, "message", None)
                if not msg or not getattr(msg, "content", None):
                    raise ValueError("Empty message content in AI response")
                text = msg.content
                try:
                    return json.loads(text)
                except Exception:
                    log.warning("AI returned non-JSON or invalid JSON; content preview=%s", (text or "")[:1000])
                    raise
            except Exception as e:
                last = e
                log.warning(
                    "AI request failed attempt=%s error_type=%s error=%s",
                    attempt + 1,
                    type(e).__name__,
                    str(e)[:500],
                )
                await asyncio.sleep(2 ** attempt)
        raise last

    async def analyze(self, text, knowledge=None, post_context=None):
        knowledge = knowledge or []
        kb = "\n".join(f"- {x}" for x in knowledge[:10])
        context = post_context.strip() if post_context else "нет"
        prompt = f'''Классифицируй комментарий. Используй базу знаний только как контекст, не придумывай фактов.
База знаний:
{kb or "нет"}
КОНТЕКСТ ПУБЛИКАЦИИ И РОДИТЕЛЬСКОГО КОММЕНТАРИЯ (если доступен):
{context}
Верни только JSON:
{{"category":"QUESTION|PRAISE|JOKE|DISCUSSION|CRITICISM|NEGATIVE|CONFLICT|SPAM|ADVERTISING|INSULT|OFF_TOPIC|OTHER",
"sentiment":"positive|neutral|negative","confidence":0.0,"summary":"кратко",
"requires_admin":true,"should_reply":false}}
Обычная реакция, благодарность или короткая похвала не требуют ответа. should_reply=true
только если комментарий действительно задаёт вопрос или требует содержательного ответа.
Комментарий:
{text}'''
        return AIAnalysisResult.model_validate(await self._json(prompt))

    async def analyze_with_reply(self, text, knowledge=None, post_context=None):
        knowledge = knowledge or []
        kb = "\n".join(f"- {x}" for x in knowledge[:10])
        context = post_context.strip() if post_context else "нет"
        prompt = f'''Проанализируй комментарий и, только если он действительно требует содержательного ответа,
сразу подготовь один короткий ответ от имени Telegram-канала «МЧС | Мы Чего-то Строим».
Не выдумывай фактов и не отвечай на обычную реакцию, благодарность, спам, рекламу или оскорбление.
КОНТЕКСТ ПУБЛИКАЦИИ И РОДИТЕЛЬСКОГО КОММЕНТАРИЯ:
{context}
База знаний:
{kb or "нет"}
Верни только JSON:
{{"category":"QUESTION|PRAISE|JOKE|DISCUSSION|CRITICISM|NEGATIVE|CONFLICT|SPAM|ADVERTISING|INSULT|OFF_TOPIC|OTHER",
"sentiment":"positive|neutral|negative","confidence":0.0,"summary":"кратко",
"requires_admin":true,"should_reply":false,"reply":""}}
Если should_reply=false, поле reply должно быть пустым.
Комментарий:
{text}'''
        return AIAutoReplyResult.model_validate(await self._json(prompt))

    async def responses(self, comment, category, knowledge=None, post_context=None):
        kb = "\n".join(f"- {x}" for x in (knowledge or [])[:10])
        context = post_context.strip() if post_context else "нет"
        prompt = f'''Создай ровно 3 разных варианта ответа на комментарий.
Категория: {category}
Комментарий: {comment}
КОНТЕКСТ ПУБЛИКАЦИИ И РОДИТЕЛЬСКОГО КОММЕНТАРИЯ:
{context}
Контекст/база знаний:
{kb or "нет"}
Если точного факта нет, не выдумывай его.
Верни только JSON:
{{"responses":[{{"variant":1,"text":"..." }},{{"variant":2,"text":"..."}},{{"variant":3,"text":"..."}}]}}
1 — коротко и дружелюбно.
2 — разговорно и живо, можно лёгкий юмор.
3 — подробнее и содержательнее.'''
        result = AIResponsesResult.model_validate(await self._json(prompt))
        if len(result.responses) != 3:
            raise ValueError("AI did not return 3 responses")
        return result.responses

    async def close(self):
        try:
            close_fn = getattr(self.client, "aclose", None) or getattr(self.client, "close", None)
            if close_fn:
                res = close_fn()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            log.exception("Error closing OpenAI client")


# Global (process-wide) service instance
_global_service: Optional[OpenAIService] = None

async def init_global_service(api_key: str, model: str, base_url: Optional[str] = None):
    global _global_service
    if _global_service is None and api_key:
        _global_service = OpenAIService(api_key, model, base_url)
        log.info("Initialized global AI service model=%s", model)
    return _global_service


def get_global_service() -> Optional[OpenAIService]:
    global _global_service
    if _global_service is None:
        settings = get_settings()
        if getattr(settings, "ai_api_key", None):
            # lazy create without awaiting
            _global_service = OpenAIService(settings.ai_api_key, settings.openai_model, getattr(settings, "ai_base_url", None))
            log.info("Lazily initialized global AI service model=%s", settings.openai_model)
    return _global_service


async def close_global_service():
    global _global_service
    if _global_service is not None:
        try:
            await _global_service.close()
        finally:
            _global_service = None
