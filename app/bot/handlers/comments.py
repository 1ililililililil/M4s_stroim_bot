import asyncio
import logging
import re
import time
from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.exc import IntegrityError
from app.config import get_settings
from app.database.database import get_session_factory
from app.database.repositories import (
    upsert_user, get_comment_by_tg, create_comment, save_analysis, save_responses,
    get_settings as db_settings, action, mark_processed, set_error,
    claim_processing, claim_auto_reply, save_published_response,
    save_post_knowledge, search_relevant_knowledge, search_faq_answers,
    create_unanswered_question, create_answer_candidate,
)
from app.services.openai_service import OpenAIService
from app.services.local_routing import FALLBACK_CONFIDENCE_THRESHOLD, select_answer_variants
from app.bot.keyboards.admin import admin_keyboard, moderation_keyboard
from app.schemas.ai import AIAnalysisResult, ResponseVariant

router = Router()
log = logging.getLogger(__name__)
SAFE = {"QUESTION","PRAISE","JOKE","DISCUSSION","CRITICISM"}
MODERATION = {"SPAM","ADVERTISING","INSULT"}
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
AD_RE = re.compile(r"\b(?:купить|заказать|реклама|скидк|казино|ставк|заработ|крипт)\w*", re.IGNORECASE)
TOXIC_RE = re.compile(r"\b(?:идиот|дебил|мразь|тупиц|пош[её]л)\w*", re.IGNORECASE)
REACTION_PHRASES = {
    "спасибо", "спасибо!", "благодарю", "круто", "класс", "огонь", "топ", "супер",
    "отлично", "молодцы", "интересно",
    "👍", "🔥", "❤️", "❤", "👏", "💪", "😊", "😂",
}
SHORT_DISCUSSION_PHRASES = {
    "не совпало",
    "совсем другая сфера",
    "у меня иначе",
    "не угадали",
    "не про меня",
}
PRAISE_WORD_RE = re.compile(
    r"\b(?:класс\w*|крут\w*|супер\w*|топ\w*|здоров\w*|интересн\w*|"
    r"уважа\w*|многогран\w*|восхищ\w*|замечательн\w*|талант\w*|"
    r"молодц\w*|приятн\w*)\b",
    re.IGNORECASE,
)
PERSONAL_RE = re.compile(
    r"(?<!\w)(?:я|мне|меня|у меня|мой|моя|мое|мои|мы|тоже|"
    r"учил(?:ся|ась|ись)?|хотел(?:а|и)?|работа\w*|жив\w*)\b",
    re.IGNORECASE,
)
PROFESSION_RE = re.compile(
    r"\b(?:професс\w*|работ\w*|учил\w*|образован\w*|специальност\w*|"
    r"бухгалтер\w*|секретар\w*|сфер\w*|должност\w*|образовани\w*)\b",
    re.IGNORECASE,
)
DISCUSSION_RE = re.compile(
    r"\b(?:жизн\w*|опыт\w*|навык\w*|иногда|получается|"
    r"совпад\w*|меня\w*|остава\w*|помн\w*)\b",
    re.IGNORECASE,
)
LOCAL_PRAISE_VARIANTS = (
    "Спасибо большое! 😄 Очень приятно!",
    "Спасибо! Рад, что вам понравилось 🔥",
    "Благодарю! Такие комментарии очень мотивируют 😊",
    "Спасибо большое! Стараюсь не зацикливаться только на одной сфере жизни 😄",
    "Очень приятно это читать! Спасибо 🙌",
)
LOCAL_DISCUSSION_VARIANTS = (
    "Вот это как раз и интересно — жизнь часто уводит совсем в другую сторону 😄",
    "Зато опыт никуда не пропадает, даже если работаешь уже совсем в другой сфере 👍",
    "Понимаю 😄 Иногда профессия меняется, а навыки всё равно остаются с нами.",
    "Получается, образование и работа могут быть совсем разными историями 😊",
    "Вот она — многогранность жизни 😄 Главное, что опыт остаётся.",
)
MAX_POST_CONTEXT_CHARS = 3500
MAX_PARENT_COMMENT_CHARS = 1200
MAX_MEMORY_CONTEXT_CHARS = 3200
MAX_MEMORY_ENTRY_CHARS = 900
QUESTION_RE = re.compile(
    r"(?:\?|"
    r"(?<!\w)(?:кто|что|где|когда|почему|зачем|как|сколько|какой|какая|какие)(?!\w)"
    r"(?=\s|$|[!,.;:])|"
    r"(?<!\w)(?:можно|правда)\s+ли(?!\w)|"
    r"(?<!\w)(?:расскажите|подскажите|объясните)(?!\w)|"
    r"(?<!\w)можно\s+подробнее(?!\w))",
    re.IGNORECASE,
)


class CommentProcessingQueue:
    def __init__(self, workers=2, maxsize=100):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._worker_count = workers
        self._workers = []

    async def start(self):
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker(), name=f"comment-worker-{index}")
            for index in range(self._worker_count)
        ]
        log.info(
            "Comment processing queue started workers=%s maxsize=%s",
            self._worker_count,
            self._queue.maxsize,
        )

    async def submit(self, bot, comment_id):
        await self._queue.put((bot, comment_id))
        log.info("PERF comment=%s queued size=%s", comment_id, self._queue.qsize())

    async def _worker(self):
        while True:
            bot, comment_id = await self._queue.get()
            try:
                await process_comment(bot, comment_id)
            except Exception:
                log.exception("Unexpected background failure for comment=%s", comment_id)
            finally:
                self._queue.task_done()

    async def stop(self):
        if not self._workers:
            return
        await self._queue.join()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        log.info("Comment processing queue stopped")


_comment_queue = CommentProcessingQueue()


async def start_comment_queue():
    await _comment_queue.start()


async def stop_comment_queue():
    await _comment_queue.stop()


async def enqueue_comment(bot, comment_id):
    if not _comment_queue._workers:
        log.warning("Comment queue is not started; processing comment=%s inline", comment_id)
        return await process_comment(bot, comment_id)
    await _comment_queue.submit(bot, comment_id)
    return True

def has_meaningful_question(text: str) -> bool:
    return bool(QUESTION_RE.search(" ".join(text.casefold().split())))


def _clip_context(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    marker = " …[контекст сокращён]… "
    available = max(0, limit - len(marker))
    head = available * 2 // 3
    return text[:head] + marker + text[-(available - head):]


def _message_text(message) -> str:
    return (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()


def _message_content_type(message) -> str:
    for attribute, content_type in (
        ("photo", "photo"),
        ("video", "video"),
        ("animation", "animation"),
        ("document", "document"),
        ("audio", "audio"),
        ("voice", "voice"),
        ("sticker", "sticker"),
        ("poll", "poll"),
        ("location", "location"),
    ):
        if getattr(message, attribute, None):
            return content_type
    return "text"


def _message_author(message) -> str:
    user = getattr(message, "from_user", None)
    if not user:
        return ""
    if getattr(user, "username", None):
        return f"@{user.username}"
    if getattr(user, "first_name", None):
        return user.first_name
    if getattr(user, "id", None):
        return f"Telegram ID {user.id}"
    return ""


def build_post_context(message) -> dict:
    """Build a bounded context from the replied post and optional parent comment."""
    direct_reply = getattr(message, "reply_to_message", None)
    if not direct_reply:
        return {
            "post_message_id": None,
            "reply_to_message_id": None,
            "post_context": None,
        }

    parent_comment = None
    post = direct_reply
    nested_parent = getattr(direct_reply, "reply_to_message", None)
    if nested_parent:
        parent_comment = direct_reply
        post = nested_parent

    post_text = _clip_context(_message_text(post), MAX_POST_CONTEXT_CHARS)
    post_type = _message_content_type(post)
    post_id = getattr(post, "message_id", None)
    post_context_parts = [
        f"КОНТЕКСТ ПУБЛИКАЦИИ (id={post_id or 'неизвестен'}, тип={post_type}):",
        post_text or "Текст публикации недоступен.",
    ]
    context_parts = list(post_context_parts)

    if parent_comment:
        parent_text = _clip_context(_message_text(parent_comment), MAX_PARENT_COMMENT_CHARS)
        parent_author = _message_author(parent_comment)
        context_parts.extend(
            [
                "КОНТЕКСТ РОДИТЕЛЬСКОГО КОММЕНТАРИЯ:",
                f"Автор: {parent_author or 'неизвестен'}",
                parent_text or "Текст родительского комментария недоступен.",
            ]
        )

    return {
        "post_message_id": post_id,
        "reply_to_message_id": getattr(direct_reply, "message_id", None),
        "post_context": "\n".join(context_parts),
        "knowledge_content": "\n".join(post_context_parts),
        "post_title": _clip_context(_message_text(post), 256) or None,
    }


def format_memory_context(entries) -> str:
    if not entries:
        return ""
    blocks = ["РЕЛЕВАНТНАЯ ИНФОРМАЦИЯ ИЗ ПРЕДЫДУЩИХ ПУБЛИКАЦИЙ:"]
    for index, entry in enumerate(entries, start=1):
        title = entry.title or f"Запись #{entry.id}"
        content = _clip_context(entry.text, MAX_MEMORY_ENTRY_CHARS)
        blocks.append(f"[{index}]\nТема: {title}\nСодержание: {content}")
    result = "\n\n".join(blocks)
    if len(result) <= MAX_MEMORY_CONTEXT_CHARS:
        return result
    marker = "\n\n…[memory context сокращён]…"
    return result[:MAX_MEMORY_CONTEXT_CHARS - len(marker)] + marker


def build_ai_context(post_context, memory_entries) -> str | None:
    parts = []
    if post_context:
        parts.append(post_context)
    else:
        parts.append("КОНТЕКСТ ТЕКУЩЕЙ ПУБЛИКАЦИИ:\nНедоступен.")
    memory_context = format_memory_context(memory_entries)
    if memory_context:
        parts.append(memory_context)
    return "\n\n".join(parts)


def is_simple_reaction(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if not normalized or has_meaningful_question(normalized):
        return False
    if normalized in REACTION_PHRASES:
        return True
    return bool(re.fullmatch(r"[\W_]+", normalized, flags=re.UNICODE))


def _meaningful_words(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё]{2,}", text.casefold(), flags=re.IGNORECASE)


def _has_multiple_sentences(text: str) -> bool:
    return len(re.findall(r"[.!?]", text)) >= 2


def local_social_category(text: str) -> str | None:
    """Return a local PRAISE/DISCUSSION category when a safe template applies."""
    normalized = " ".join(text.casefold().split())
    words = _meaningful_words(normalized)
    if (
        not normalized
        or is_simple_reaction(normalized)
        or has_meaningful_question(normalized)
        or normalized in SHORT_DISCUSSION_PHRASES
    ):
        return None

    praise = bool(PRAISE_WORD_RE.search(normalized))
    personal = bool(PERSONAL_RE.search(normalized))
    profession = bool(PROFESSION_RE.search(normalized))
    discussion = bool(DISCUSSION_RE.search(normalized))
    enough_text = len(words) >= 4 or _has_multiple_sentences(normalized)

    if praise and len(words) >= 3:
        return "PRAISE"
    if enough_text and (personal or profession or discussion):
        return "DISCUSSION"
    return None


def local_social_analysis(text: str, comment_id: int) -> tuple[AIAnalysisResult, list[ResponseVariant]] | None:
    category = local_social_category(text)
    if not category:
        return None
    variants = (
        LOCAL_PRAISE_VARIANTS
        if category == "PRAISE"
        else LOCAL_DISCUSSION_VARIANTS
    )
    start = comment_id % len(variants)
    responses = [
        ResponseVariant(variant=index, text=value)
        for index, value in enumerate(
            variants[start:] + variants[:start],
            start=1,
        )
    ]
    analysis = AIAnalysisResult(
        category=category,
        sentiment="positive" if category == "PRAISE" else "neutral",
        confidence=0.96,
        summary="Содержательный комментарий обработан локальным шаблоном.",
        requires_admin=False,
        should_reply=True,
    )
    return analysis, responses


def precheck_comment(text: str) -> AIAnalysisResult | None:
    if is_simple_reaction(text):
        return AIAnalysisResult(
            category="PRAISE", sentiment="positive", confidence=1.0,
            summary="Простая реакция или благодарность без содержательного вопроса.",
            requires_admin=False, should_reply=False,
        )
    normalized = " ".join(text.casefold().split())
    if normalized in SHORT_DISCUSSION_PHRASES:
        return AIAnalysisResult(
            category="DISCUSSION", sentiment="neutral", confidence=1.0,
            summary="Короткое утверждение без содержательной детали.",
            requires_admin=False, should_reply=False,
        )
    if TOXIC_RE.search(text):
        return AIAnalysisResult(
            category="INSULT", sentiment="negative", confidence=1.0,
            summary="Обнаружена потенциально оскорбительная лексика.",
            requires_admin=True, should_reply=False,
        )
    if URL_RE.search(text) and (AD_RE.search(text) or len(URL_RE.findall(text)) > 1):
        return AIAnalysisResult(
            category="ADVERTISING", sentiment="neutral", confidence=1.0,
            summary="Обнаружена ссылка или рекламный признак.",
            requires_admin=True, should_reply=False,
        )
    if AD_RE.search(text):
        return AIAnalysisResult(
            category="SPAM", sentiment="neutral", confidence=1.0,
            summary="Обнаружен потенциальный спам.",
            requires_admin=True, should_reply=False,
        )
    return None


def local_analysis(match) -> AIAnalysisResult:
    return AIAnalysisResult(
        category="QUESTION",
        sentiment="neutral",
        confidence=match.confidence,
        summary=f"Локальный ответ найден в базе знаний ({match.category}).",
        requires_admin=False,
        should_reply=True,
    )


def local_responses(match, comment_id: int) -> list[ResponseVariant]:
    return [
        ResponseVariant(variant=index, text=text)
        for index, text in enumerate(
            select_answer_variants(match.entry, comment_id),
            start=1,
        )
    ]

def normalize_analysis(analysis: AIAnalysisResult, text: str) -> AIAnalysisResult:
    if is_simple_reaction(text):
        analysis.should_reply = False
        analysis.requires_admin = False
    return analysis

async def process_comment(bot, comment_id, notify=True):
    started = time.perf_counter()
    log.info("PERF comment=%s processing_started", comment_id)
    try:
        return await _process_comment(bot, comment_id, notify)
    finally:
        log.info(
            "PERF comment=%s full_processing_ms=%.2f",
            comment_id,
            (time.perf_counter() - started) * 1000,
        )


async def _process_comment(bot, comment_id, notify=True):
    settings = get_settings()
    Session = get_session_factory()
    async with Session() as s:
        from app.database.models import Comment
        comment = await s.get(Comment, comment_id)
        if not comment:
            log.info("SKIPPED: missing comment=%s", comment_id)
            return False
        if comment.processed:
            log.info("Comment skipped: already processed comment=%s", comment_id)
            return False
        if comment.auto_reply_sent:
            log.info("Comment skipped: auto reply already published comment=%s", comment_id)
            return False
        if comment.processing:
            log.info("Comment skipped: already processing comment=%s", comment_id)
            return False
        if comment.last_error:
            log.info("Comment skipped: previous processing failure comment=%s", comment_id)
            return False
        if not await claim_processing(s, comment_id):
            log.info("Comment skipped: processing claim lost comment=%s", comment_id)
            return False
        db_started = time.perf_counter()
        await s.commit()
        log.info(
            "PERF comment=%s database_claim_write_ms=%.2f",
            comment_id,
            (time.perf_counter() - db_started) * 1000,
        )

        try:
            cfg = await db_settings(s)

            precheck_started = time.perf_counter()
            precheck = precheck_comment(comment.text)
            log.info(
                "PERF comment=%s spam_precheck_ms=%.2f result=%s",
                comment_id,
                (time.perf_counter() - precheck_started) * 1000,
                precheck.category if precheck else "clear",
            )

            ai = None
            auto_reply_text = ""
            responses = []
            route = None
            faq_match = None
            comment.ai_requested = False
            comment.ai_failed = False
            comment.fallback_used = False

            async def handle_ai_failure(error):
                nonlocal ai, analysis, responses, auto_reply_text, route
                comment.ai_failed = True
                log.warning(
                    "AI failed, starting fallback search comment=%s error_type=%s",
                    comment_id,
                    type(error).__name__,
                )
                fallback_matches = await search_faq_answers(
                    s,
                    comment.text,
                    comment.chat_id,
                    limit=5,
                )
                fallback_match = next(
                    (match for match in fallback_matches if match.confident),
                    None,
                )
                if fallback_match:
                    fallback_responses = local_responses(fallback_match, comment_id)
                    if fallback_responses:
                        ai = None
                        route = "FALLBACK"
                        comment.fallback_used = True
                        analysis = local_analysis(fallback_match)
                        responses = fallback_responses
                        auto_reply_text = responses[0].text
                        log.info(
                            "Fallback answer found comment=%s confidence=%.3f",
                            comment_id,
                            fallback_match.confidence,
                        )
                        return True

                route = "UNANSWERED"
                comment.route = route
                await create_unanswered_question(
                    s,
                    comment.chat_id,
                    comment_id,
                    comment.text,
                    comment.post_text,
                    type(error).__name__,
                )
                await set_error(s, comment_id, error)
                await s.commit()
                log.info("Unanswered question stored comment=%s", comment_id)
                if notify:
                    for admin_id in settings.admins:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ Не удалось подготовить безопасный ответ для комментария "
                                f"#{comment_id}. Вопрос сохранён для разбора.",
                            )
                        except Exception:
                            log.exception("Cannot notify admin %s", admin_id)
                return False

            if precheck:
                analysis = precheck
                if analysis.category == "PRAISE":
                    route = "FAST_REACTION"
                    log.info("SKIPPED: simple reaction comment=%s", comment_id)
                elif analysis.category == "DISCUSSION" and not analysis.should_reply:
                    route = "SKIPPED"
                    log.info("SKIPPED: short discussion comment=%s", comment_id)
                else:
                    route = "MODERATION"
                    log.info(
                        "SKIPPED: spam/moderation comment=%s category=%s",
                        comment_id,
                        analysis.category,
                    )
            else:
                social_result = local_social_analysis(comment.text, comment_id)
                if social_result:
                    analysis, responses = social_result
                    route = "FAST" if analysis.category == "PRAISE" else "SMART"
                    auto_reply_text = responses[0].text if responses else ""
                    log.info(
                        "%s local social route selected comment=%s category=%s",
                        route,
                        comment_id,
                        analysis.category,
                    )
                else:
                    faq_started = time.perf_counter()
                    log.info("SMART search started comment=%s query=%s", comment_id, comment.text[:200])
                    faq_matches = await search_faq_answers(
                        s,
                        comment.text,
                        comment.chat_id,
                        limit=5,
                    )
                    faq_match = faq_matches[0] if faq_matches else None
                    log.info(
                        "SMART search completed comment=%s results=%s best_confidence=%s",
                        comment_id,
                        len(faq_matches),
                        f"{faq_match.confidence:.3f}" if faq_match else "none",
                    )
                    if faq_match and faq_match.confident:
                        route = faq_match.layer
                        analysis = local_analysis(faq_match)
                        responses = local_responses(faq_match, comment_id)
                        auto_reply_text = responses[0].text if responses else ""
                        log.info(
                            "%s route selected comment=%s confidence=%.3f matched=%s",
                            route,
                            comment_id,
                            faq_match.confidence,
                            ",".join(faq_match.matched_keywords),
                        )
                    else:
                        log.info(
                            "SMART confidence below threshold comment=%s threshold=%.2f",
                            comment_id,
                            FALLBACK_CONFIDENCE_THRESHOLD,
                        )
                    if not comment.post_text:
                        log.info(
                            "Post context unavailable, processing comment without post context comment=%s",
                            comment_id,
                        )
                    if not faq_match or not faq_match.confident:
                        search_started = time.perf_counter()
                        memory_entries = await search_relevant_knowledge(
                            s,
                            comment.text,
                            comment.chat_id,
                            current_post_id=comment.post_message_id,
                            limit=3,
                        )
                        log.info(
                            "Knowledge search completed comment=%s results=%s",
                            comment_id,
                            len(memory_entries),
                        )
                        ai_context = build_ai_context(comment.post_text, memory_entries)
                        ai = OpenAIService(
                            settings.ai_api_key,
                            settings.openai_model,
                            settings.ai_base_url,
                        )
                        comment.ai_requested = True
                        route = "AI"
                        ai_started = time.perf_counter()
                        mode = "combined" if cfg.auto_reply_enabled else "analysis"
                        log.info("PERF comment=%s ai_analysis_started mode=%s", comment_id, mode)
                        try:
                            if cfg.auto_reply_enabled:
                                combined = await ai.analyze_with_reply(
                                    comment.text,
                                    [x.text for x in memory_entries],
                                    ai_context,
                                )
                                analysis = combined
                                auto_reply_text = combined.reply.strip()
                            else:
                                analysis = await ai.analyze(
                                    comment.text,
                                    [x.text for x in memory_entries],
                                    ai_context,
                                )
                        except Exception as error:
                            if not await handle_ai_failure(error):
                                return False
                        else:
                            log.info(
                                "PERF comment=%s ai_analysis_finished duration_ms=%.2f",
                                comment_id,
                                (time.perf_counter() - ai_started) * 1000,
                            )
                            log.info("AI analysis completed comment=%s", comment_id)

            analysis = normalize_analysis(analysis, comment.text)
            comment.route = route or "SKIPPED"
            await save_analysis(s, comment_id, analysis)

            if analysis.category in SAFE and analysis.should_reply:
                auto_eligible = (
                    cfg.auto_reply_enabled
                    and analysis.confidence >= cfg.auto_reply_threshold
                    and not analysis.requires_admin
                    and bool(auto_reply_text)
                )
                if auto_eligible:
                    responses = [ResponseVariant(variant=1, text=auto_reply_text)]
                elif ai:
                    response_started = time.perf_counter()
                    log.info("PERF comment=%s response_generation_started", comment_id)
                    try:
                        responses = await ai.responses(
                            comment.text,
                            analysis.category,
                            [],
                            ai_context,
                        )
                    except Exception as error:
                        if not await handle_ai_failure(error):
                            return False
                        auto_eligible = (
                            cfg.auto_reply_enabled
                            and analysis.confidence >= cfg.auto_reply_threshold
                            and not analysis.requires_admin
                            and bool(auto_reply_text)
                        )
                    log.info(
                        "PERF comment=%s response_generation_finished duration_ms=%.2f",
                        comment_id,
                        (time.perf_counter() - response_started) * 1000,
                    )
                if responses:
                    await save_responses(s, comment_id, responses)
                    if route == "AI" and analysis.category in SAFE and analysis.should_reply:
                        await create_answer_candidate(
                            s,
                            comment.chat_id,
                            comment_id,
                            comment.text,
                            responses[0].text,
                            category=analysis.category,
                            post_context=comment.post_text,
                        )
                        log.info("Candidate answer stored comment=%s", comment_id)

                if cfg.auto_reply_enabled and auto_eligible and await claim_auto_reply(s, comment_id):
                    first = responses[0]
                    db_started = time.perf_counter()
                    await s.commit()
                    log.info(
                        "PERF comment=%s database_analysis_write_ms=%.2f",
                        comment_id,
                        (time.perf_counter() - db_started) * 1000,
                    )
                    send_started = time.perf_counter()
                    log.info(
                        "PERF comment=%s telegram_send_started chat_id=%s reply_to=%s",
                        comment_id,
                        comment.chat_id,
                        comment.telegram_message_id,
                    )
                    try:
                        await bot.send_message(
                            comment.chat_id,
                            first.text,
                            reply_to_message_id=comment.telegram_message_id,
                        )
                    except Exception as e:
                        # Keep the claim: a retry after an uncertain Telegram
                        # response could publish the same reply twice.
                        log.error(
                            "ERROR: Telegram send failed comment=%s error_type=%s error=%s",
                            comment_id,
                            type(e).__name__,
                            str(e)[:1000],
                        )
                        raise
                    log.info(
                        "PERF comment=%s telegram_send_finished duration_ms=%.2f",
                        comment_id,
                        (time.perf_counter() - send_started) * 1000,
                    )
                    async with Session() as publish_session:
                        await save_published_response(publish_session, comment_id, first.text)
                        await action(publish_session, comment_id, 0, "auto_reply", first.variant)
                        await mark_processed(publish_session, comment_id)
                        db_started = time.perf_counter()
                        await publish_session.commit()
                        log.info(
                            "PERF comment=%s database_publish_write_ms=%.2f",
                            comment_id,
                            (time.perf_counter() - db_started) * 1000,
                        )
                        log.info("Auto reply published comment=%s", comment_id)
                    return True
                if cfg.auto_reply_enabled:
                    if analysis.confidence < cfg.auto_reply_threshold:
                        log.info(
                            "SKIPPED: confidence below threshold comment=%s confidence=%.3f threshold=%.3f",
                            comment_id,
                            analysis.confidence,
                            cfg.auto_reply_threshold,
                        )
                    elif analysis.requires_admin:
                        log.info("SKIPPED: requires admin comment=%s", comment_id)
                    elif not auto_reply_text:
                        log.info("SKIPPED: empty AI reply comment=%s", comment_id)
                else:
                    log.info("SKIPPED: auto replies disabled comment=%s", comment_id)
            elif not analysis.should_reply:
                log.info("SKIPPED: needs_reply=false comment=%s", comment_id)
            elif analysis.category not in SAFE:
                log.info(
                    "SKIPPED: unsupported category comment=%s category=%s",
                    comment_id,
                    analysis.category,
                )

            if analysis.category not in MODERATION and not responses:
                await mark_processed(s, comment_id)
            else:
                await s.refresh(comment)
                comment.processing = False
            db_started = time.perf_counter()
            await s.commit()
            log.info(
                "PERF comment=%s database_status_write_ms=%.2f",
                comment_id,
                (time.perf_counter() - db_started) * 1000,
            )
        except Exception as e:
            await set_error(s, comment_id, e)
            await s.commit()
            log.exception("Comment %s processing failed; polling continues", comment_id)
            if notify:
                for admin_id in settings.admins:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"⚠️ AI временно недоступен для комментария #{comment_id}. "
                            "Комментарий сохранён без пустого ответа.",
                        )
                    except Exception:
                        log.exception("Cannot notify admin %s about error", admin_id)
            return False

    if not notify:
        return True

    if analysis.category in MODERATION:
        text = f"⚠️ Требуется модерация\n\n💬 {comment.text}\n\n🏷 {analysis.category}"
        keyboard = moderation_keyboard(comment_id)
    elif responses:
        text = (f"💬 Новый комментарий\n\n📝 {comment.text}\n\n"
                f"🏷 Категория: {analysis.category}\n🎯 Уверенность: {analysis.confidence:.0%}\n\n🤖 Варианты:\n\n" +
                "\n\n".join(f"{r.variant}️⃣ {r.text}" for r in responses))
        keyboard = admin_keyboard(comment_id)
    else:
        text = f"💬 Новый комментарий\n\n📝 {comment.text}\n\n🏷 {analysis.category}\n⏭ Ответ не требуется."
        keyboard = None

    for admin_id in settings.admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=keyboard)
        except Exception:
            log.exception("Cannot notify admin %s", admin_id)
    return True

@router.message(F.chat.type.in_({"group","supergroup"}))
async def handle_comment(message: Message):
    started = time.perf_counter()
    log.info(
        "PERF comment update_received chat_id=%s message_id=%s",
        message.chat.id,
        message.message_id,
    )
    if not message.from_user or message.from_user.is_bot or not message.text:
        log.info(
            "SKIPPED: bot/empty message chat_id=%s message_id=%s",
            message.chat.id,
            message.message_id,
        )
        return

    Session = get_session_factory()
    async with Session() as s:
        try:
            duplicate_started = time.perf_counter()
            existing = await get_comment_by_tg(s, message.chat.id, message.message_id)
            log.info(
                "PERF comment update_duplicate_check_ms=%.2f",
                (time.perf_counter() - duplicate_started) * 1000,
            )
            if existing:
                if existing.processed:
                    reason = "already processed"
                elif existing.auto_reply_sent:
                    reason = "auto reply already published"
                elif existing.processing:
                    reason = "already processing"
                else:
                    reason = "already queued"
                log.info(
                    "SKIPPED: %s chat_id=%s message_id=%s",
                    reason,
                    message.chat.id,
                    message.message_id,
                )
                return
            user = await upsert_user(s, message.from_user.id, message.from_user.username, message.from_user.first_name)
            context = build_post_context(message)
            if context["post_message_id"] and context.get("knowledge_content"):
                await save_post_knowledge(
                    s,
                    message.chat.id,
                    context["post_message_id"],
                    context["knowledge_content"],
                    title=context.get("post_title"),
                )
            comment = await create_comment(
                s,
                telegram_message_id=message.message_id,
                chat_id=message.chat.id,
                user_id=user.id,
                post_message_id=context["post_message_id"],
                post_text=context["post_context"],
                text=message.text,
                reply_to_message_id=context["reply_to_message_id"],
            )
            db_started = time.perf_counter()
            await s.commit()
            cid = comment.id
            log.info(
                "PERF comment=%s database_comment_write_ms=%.2f",
                cid,
                (time.perf_counter() - db_started) * 1000,
            )
        except IntegrityError:
            await s.rollback()
            log.info(
                "Duplicate comment prevented chat=%s message=%s",
                message.chat.id,
                message.message_id,
            )
            return

    try:
        await enqueue_comment(message.bot, cid)
        log.info(
            "PERF comment=%s handler_queue_submit_ms=%.2f",
            cid,
            (time.perf_counter() - started) * 1000,
        )
    except Exception:
        # process_comment handles expected AI/Telegram errors itself. This
        # guard protects the handler from an unexpected infrastructure error.
        log.exception("Unexpected comment handler failure for comment %s", cid)
