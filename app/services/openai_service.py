import asyncio, json
import logging
import time
from openai import AsyncOpenAI
from app.schemas.ai import AIAnalysisResult, AIAutoReplyResult, AIResponsesResult
from typing import Optional
from app.config import get_settings
import re


log = logging.getLogger(__name__)

SYSTEM = '''Ты AI-администратор Telegram-канала «МЧС | Мы Чего-то Строим».

Тематика: пожарная служба, МЧС, работа пожарных, реальные выезды, техника, экипировка, обучение, физподгото.[...]

Стиль: живой, дружелюбный, разговорный, уважительный, иногда лёгкий юмор. Без канцелярита и роботизированн�[...]

Нельзя придумывать факты, выдавать догадки за факты, давать опасные инструкции, раскрывать личную или служ[...]

Найденную память предыдущих публикаций используй только как дополнительный контекст. Текущий пост имеет �[...]
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

ВАЖНО — порядок приоритетов при анализе (сначала самое важное):
1) Точный смысл комментария (что именно сказал пользователь).
2) Контекст исходного поста.
3) Эмоция и намерение автора комментария.
4) Категория комментария — влияет только на стиль ответа, но НЕ заменяет анализ содержания.
5) Стиль ответов канала.

Шаги, которые нужно выполнить перед ответом:
- Сначала внимательно проанализируй конкретный комментарий и выдели его основную мысль.
- Определи выраженную эмоцию или отношение.
- Учти контекст публикации только как дополнительную информацию.
- Не придумывай фактов и не переходи на другую тему.

Верни только JSON:
{"category":"QUESTION|PRAISE|JOKE|DISCUSSION|CRITICISM|NEGATIVE|CONFLICT|SPAM|ADVERTISING|INSULT|OFF_TOPIC|OTHER",
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

ВАЖНО — порядок приоритетов при генерации ответа (сначала самое важное):
1) Точный смысл комментария.
2) Контекст исходного поста.
3) Эмоция и намерение автора комментария.
4) Категория комментария — только как подсказка для стиля, НЕ как замена содержания.
5) Стиль ответов канала.

Перед тем как сформировать reply:
- Сначала внимательно проанализируй конкретный комментарий и определи, о чём именно говорит пользователь.
- Определи выраженную эмоцию/намерение.
- Каждый вариант ответа (здесь один) должен быть прямым, естественным ответом на комментарий.
- Не уходи в другую тему и не генерируй абстрактные рассуждения.

КОНТЕКСТ ПУБЛИКАЦИИ И РОДИТЕЛЬСКОГО КОММЕНТАРИЯ:
{context}
База знаний:
{kb or "нет"}
Верни только JSON:
{"category":"QUESTION|PRAISE|JOKE|DISCUSSION|CRITICISM|NEGATIVE|CONFLICT|SPAM|ADVERTISING|INSULT|OFF_TOPIC|OTHER",
"sentiment":"positive|neutral|negative","confidence":0.0,"summary":"кратко",
"requires_admin":true,"should_reply":false,"reply":""}}
Если should_reply=false, поле reply должно быть пустым.
Комментарий:
{text}'''
        return AIAutoReplyResult.model_validate(await self._json(prompt))

    def _extract_meaningful_words(self, text: str, limit=8) -> list[str]:
        if not text:
            return []
        # keep Russian and Latin letters, words of length >=3
        words = re.findall(r"[a-zа-яё]{3,}", text.lower(), flags=re.IGNORECASE)
        # filter common stopwords that are not meaningful in topics
        stop = {"это", "такое", "очень", "все", "всё", "своё", "много", "мой", "мне", "мы", "вы", "по"}
        seen = set()
        out = []
        for w in words:
            if w in stop:
                continue
            if w in seen:
                continue
            seen.add(w)
            out.append(w)
            if len(out) >= limit:
                break
        return out

    def _stem(self, word: str, n: int = 5) -> str:
        return word[:n]

    def _contains_topic_stem(self, response: str, stems: set[str]) -> bool:
        if not stems:
            return False
        # check if any stem appears in response words
        resp_words = re.findall(r"[a-zа-яё]{3,}", (response or "").lower(), flags=re.IGNORECASE)
        for rw in resp_words:
            for s in stems:
                if rw.startswith(s) or s.startswith(rw[:len(s)]):
                    return True
        return False

    def _is_response_relevant(self, response_text: str, comment: str, post_context: str | None) -> bool:
        response = (response_text or "").lower()
        # meaningful stems from comment and context
        comment_words = self._extract_meaningful_words(comment, limit=8)
        comment_stems = {self._stem(w) for w in comment_words}
        context_words = self._extract_meaningful_words(post_context or "", limit=8)
        context_stems = {self._stem(w) for w in context_words}

        # Topic keyword groups (not exhaustive) to allow paraphrases
        event_keywords = {"меропр", "массов", "мероприят", "толп", "толпа", "люд", "людей", "скоплен", "скопл", "событ", "концерт", "фестиваль", "сбор"}
        crowd_paraphrases = {"много людей", "большие скопления", "большие скоплен", "скопление людей", "много народу", "мног нар"}
        work_keywords = {"опыт", "профес", "работ", "навык", "учеб", "обучен", "карьер", "школ", "унив", "курс"}

        # 1) If response directly contains topical stems from the comment => relevant
        if self._contains_topic_stem(response, comment_stems):
            return True

        # 1b) if comment clearly refers to event/crowd even without shared stems, detect via event keywords in comment
        comment_lower = (comment or "").lower()
        comment_is_event = any(k in comment_lower for k in event_keywords)
        # also check for common paraphrase words in comment
        for p in ["толпа", "скоплен", "людей", "мног", "массов"]:
            if p in comment_lower:
                comment_is_event = True
                break

        # 2) If response contains event-related words/paraphrases and comment is event-related -> relevant
        if comment_is_event:
            # check response for event keywords or common paraphrases
            if any(k in response for k in event_keywords):
                return True
            if any(p in response for p in crowd_paraphrases):
                return True
            # also allow synonyms like "камерн", "небольшая группа" expressed as words
            if "камер" in response or "не всем" in response or "не комфорт" in response or "комфортно" in response:
                # these are often contextual reactions to crowd discomfort
                return True

        # 2b) If response contains topical stems only from context but not from comment => NOT relevant
        if self._contains_topic_stem(response, context_stems) and not comment_is_event:
            return False

        # 3) Negative-topic guard: if response introduces topics like profession/experience/work/training but comment/context do not mention them => reject
        for t in work_keywords:
            if t in response:
                if not any(t.startswith(s) or s.startswith(t[:len(s)]) for s in comment_stems.union(context_stems)):
                    return False

        # 4) Reaction-only disallowed: if response contains only an emotion token but no topical relation -> reject
        emotion_tokens = ["понимаю", "сожале", "надеюсь", "рад", "здорово", "спасибо", "пожалуйста", "удачи", "сочувствую"]
        has_emotion = any(t in response for t in emotion_tokens)
        has_any_topic = (self._contains_topic_stem(response, comment_stems) or any(k in response for k in event_keywords))
        if has_emotion and not has_any_topic:
            # emotion alone is not sufficient to claim relevance
            return False

        # 5) Fallback: allow short confirmations only if they are not introducing new topics and seem like direct social reactions
        if len(response.split()) <= 8 and has_emotion:
            for t in work_keywords:
                if t in response:
                    return False
            return True

        # default: require topical relation — reject otherwise
        return False

    async def responses(self, comment, category, knowledge=None, post_context=None):
        kb = "\n".join(f"- {x}" for x in (knowledge or [])[:10])
        context = post_context.strip() if post_context else "нет"
        prompt = f'''Сначала внимательно проанализируй конкретный комментарий и выдели, о чём именно говорит пользователь.
Определи эмоцию или отношение автора.
Используй контекст публикации только как дополнительную информацию.
Категорию ({category}) учитывай ТОЛЬКО для тона/стиля ответа, но НЕ замещай анализ содержания комментария.

Создай ровно 3 разных варианта ответа на комментарий. Каждый вариант должен быть прямым, естественным ответом именно на этот комментарий.
- Вариант 1: коротко и дружелюбно.
- Вариант 2: разговорно и живо, можно лёгкий юмор.
- Вариант 3: подробнее и содержательнее.

Строгие правила:
- Не уходи в другую тему.
- Не генерируй абстрактные философские рассуждения.
- Не используй шаблонные универсальные фразы, которые подходят к любому комментарию.
- Не придумывай фактов, которых нет в комментарии или контексте поста.
- Если комментарий говорит о мероприятии, ответ должен быть связан именно с мероприятием.
- Если комментарий выражает сожаление, радость, вопрос, благодарность или личный опыт — ответ должен реаги[...]

КОНТЕКСТ ПУБЛИКАЦИИ И РОДИТЕЛЬСКОГО КОММЕНТАРИЯ:
{context}
Контекст/база знаний:
{kb or "нет"}

Верни только JSON:
{{"responses":[{{"variant":1,"text":"..." }},{{"variant":2,"text":"..."}},{{"variant":3,"text":"..."}}]}}

После составления ответов выполни внутреннюю проверку: для каждого варианта мысленно проверь — "Можно ли естественно использовать этот ответ как прямую реакцию именно на данный комментарий?" Если вариант не является прямой реакцией — не возвращай его.'''
        result = AIResponsesResult.model_validate(await self._json(prompt))
        if len(result.responses) != 3:
            raise ValueError("AI did not return 3 responses")

        # Internal relevance check — reject if any response appears unrelated
        bad = []
        for r in result.responses:
            if not self._is_response_relevant(r.text, comment or "", post_context or ""):
                bad.append((r.variant, r.text))
        if bad:
            # Log details and raise to let caller handle fallback/retry
            log.warning("AI returned responses that failed relevance check: %s", bad)
            raise ValueError("AI returned irrelevant responses")

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
