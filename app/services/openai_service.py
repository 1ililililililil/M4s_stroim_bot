import asyncio, json
import logging
import time
from openai import AsyncOpenAI
from app.schemas.ai import AIAnalysisResult, AIAutoReplyResult, AIResponsesResult


log = logging.getLogger(__name__)

SYSTEM = '''Ты AI-администратор Telegram-канала «МЧС | Мы Чего-то Строим».
Тематика: пожарная служба, МЧС, работа пожарных, реальные выезды, техника, экипировка, обучение, физподготовка, будни и юмор.
Стиль: живой, дружелюбный, разговорный, уважительный, иногда лёгкий юмор. Без канцелярита и роботизированности.
Нельзя придумывать факты, выдавать догадки за факты, давать опасные инструкции, раскрывать личную или служебную информацию, грубить или провоцировать конфликты.
Найденную память предыдущих публикаций используй только как дополнительный контекст. Текущая публикация и текущий комментарий имеют более высокий приоритет. Если связь с памятью неочевидна или данных недостаточно, не выдумывай факты и отвечай только на основании доступного текущего контекста.'''

class OpenAIService:
    def __init__(self, api_key, model, base_url=None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30)
        self.model = model

    async def _json(self, prompt: str):
        last = None
        for attempt in range(3):
            try:
                r = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}],
                    max_tokens=8192,
                    response_format={"type":"json_object"},
                    temperature=0.4,
                )
                return json.loads(r.choices[0].message.content)
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
        prompt = f'''Классифицируй комментарий. Используй базу знаний только как контекст, не придумывай факты.
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
Не выдумывай факты и не отвечай на обычную реакцию, благодарность, спам, рекламу или оскорбление.
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
