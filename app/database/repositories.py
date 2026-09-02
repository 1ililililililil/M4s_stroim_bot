from datetime import datetime
import json
import re

from sqlalchemy import select, func, desc, update, or_, and_
from .models import (
    User, Comment, AIAnalysis, AIResponse, AdminAction, BotSettings,
    KnowledgeBase, UnansweredQuestion, AnswerCandidate,
)
from app.services.local_routing import score_entry

async def upsert_user(s, telegram_id, username, first_name):
    user = await s.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        s.add(user); await s.flush()
    else:
        user.username, user.first_name = username, first_name
    return user

async def get_comment_by_tg(s, chat_id, message_id):
    return await s.scalar(select(Comment).where(Comment.chat_id == chat_id, Comment.telegram_message_id == message_id))

async def create_comment(s, **kwargs):
    obj = Comment(**kwargs); s.add(obj); await s.flush(); return obj

async def claim_processing(s, comment_id):
    result = await s.execute(
        update(Comment)
        .where(Comment.id == comment_id, Comment.processed.is_(False), Comment.processing.is_(False))
        .values(processing=True)
    )
    return result.rowcount == 1

async def save_analysis(s, comment_id, result):
    old = await s.scalar(select(AIAnalysis).where(AIAnalysis.comment_id == comment_id))
    if old:
        old.category, old.sentiment, old.confidence = result.category, result.sentiment, result.confidence
        old.summary, old.requires_admin = result.summary, result.requires_admin
        return old
    obj = AIAnalysis(comment_id=comment_id, category=result.category, sentiment=result.sentiment,
                     confidence=result.confidence, summary=result.summary, requires_admin=result.requires_admin)
    s.add(obj); await s.flush(); return obj

async def save_responses(s, comment_id, responses):
    for r in responses:
        s.add(AIResponse(comment_id=comment_id, variant_number=r.variant, text=r.text))
    await s.flush()

async def get_response(s, comment_id, variant):
    return await s.scalar(select(AIResponse).where(
        AIResponse.comment_id == comment_id, AIResponse.variant_number == variant
    ).order_by(desc(AIResponse.id)))

async def mark_processed(s, comment_id):
    obj = await s.get(Comment, comment_id)
    if obj:
        obj.processed = True
        obj.processing = False
        obj.processed_at = datetime.utcnow()

async def release_processing(s, comment_id):
    obj = await s.get(Comment, comment_id)
    if obj:
        obj.processing = False

async def claim_auto_reply(s, comment_id):
    result = await s.execute(
        update(Comment)
        .where(
            Comment.id == comment_id,
            Comment.processed.is_(False),
            Comment.processing.is_(True),
            Comment.auto_reply_sent.is_(False),
        )
        .values(auto_reply_sent=True)
    )
    return result.rowcount == 1

async def save_published_response(s, comment_id, response):
    obj = await s.get(Comment, comment_id)
    if obj:
        obj.published_response = response

async def set_error(s, comment_id, error):
    obj = await s.get(Comment, comment_id)
    if obj:
        obj.retry_count += 1
        obj.last_error = str(error)[:1000]
        obj.processing = False

async def action(s, comment_id, admin_id, name, variant=None):
    s.add(AdminAction(comment_id=comment_id, admin_id=admin_id, action=name, selected_variant=variant))

async def get_settings(s):
    obj = await s.get(BotSettings, 1)
    if not obj:
        obj = BotSettings(id=1, auto_reply_enabled=False, auto_reply_threshold=0.95)
        s.add(obj); await s.flush()
    return obj

async def add_knowledge(s, text, chat_id=None, source_type="manual", source_message_id=None, title=None):
    obj = KnowledgeBase(
        text=text.strip(),
        chat_id=chat_id,
        source_type=source_type,
        source_message_id=source_message_id,
        title=title.strip()[:256] if title else None,
    )
    s.add(obj); await s.flush(); return obj


async def add_faq_answer(
    s,
    question,
    keywords,
    answer,
    chat_id=None,
    category="GENERAL",
    priority=0,
    variants=None,
):
    values = [str(value).strip() for value in (variants or []) if str(value).strip()]
    primary = answer.strip()
    if primary and primary not in values:
        values.insert(0, primary)
    obj = KnowledgeBase(
        text=primary,
        chat_id=chat_id,
        source_type="faq",
        title=question.strip()[:256],
        question=question.strip(),
        keywords=(keywords or "").strip()[:2000] or None,
        category=(category or "GENERAL").strip().upper()[:32],
        priority=priority,
        answer_variants=json.dumps(values, ensure_ascii=False),
    )
    s.add(obj)
    await s.flush()
    return obj


async def list_faq_answers(s, chat_id=None, limit=50, offset=0):
    conditions = [
        KnowledgeBase.active.is_(True),
        KnowledgeBase.source_type == "faq",
    ]
    if chat_id is not None:
        conditions.append(or_(KnowledgeBase.chat_id == chat_id, KnowledgeBase.chat_id.is_(None)))
    return (await s.scalars(
        select(KnowledgeBase).where(*conditions)
        .order_by(desc(KnowledgeBase.priority), desc(KnowledgeBase.id))
        .offset(max(0, offset))
        .limit(limit)
    )).all()


async def search_faq_answers(s, query, chat_id=None, limit=5):
    entries = await list_faq_answers(s, chat_id=chat_id, limit=200)
    matches = [match for entry in entries if (match := score_entry(query, entry))]
    matches.sort(
        key=lambda match: (
            match.confidence,
            getattr(match.entry, "priority", 0),
            match.entry.id,
        ),
        reverse=True,
    )
    return matches[:max(0, limit)]


async def seed_default_faqs(s):
    defaults = [
        {
            "question": "Как вызвать пожарных?",
            "keywords": "101, 112, вызвать пожарных, позвонить",
            "answer": "Позвоните по номеру 101 или 112 и сообщите точный адрес и что произошло.",
            "variants": [
                "Позвоните по номеру 101 или 112 и сообщите точный адрес и что произошло.",
                "При пожаре звоните 101 или 112. Назовите точный адрес и кратко опишите ситуацию.",
            ],
            "category": "EMERGENCY",
            "priority": 100,
        },
        {
            "question": "Что делать при пожаре?",
            "keywords": "пожар, огонь, безопасность, эвакуация",
            "answer": "Сразу сообщите о пожаре по номеру 101 или 112, предупредите окружающих и покиньте опасную зону, если это безопасно.",
            "variants": [
                "Сразу сообщите о пожаре по номеру 101 или 112, предупредите окружающих и покиньте опасную зону, если это безопасно.",
                "Позвоните 101 или 112, предупредите людей рядом и уходите из опасной зоны, если путь безопасен.",
            ],
            "category": "FIRE_SAFETY",
            "priority": 100,
        },
        {
            "question": "Что делать при задымлении?",
            "keywords": "дым, задымление, выход, эвакуация",
            "answer": "Старайтесь не вдыхать дым, двигайтесь к выходу как можно ниже к полу и покиньте помещение, если путь безопасен.",
            "variants": [
                "Старайтесь не вдыхать дым, двигайтесь к выходу как можно ниже к полу и покиньте помещение, если путь безопасен.",
                "При задымлении держитесь ниже к полу, не вдыхайте дым и выходите только безопасным путём.",
            ],
            "category": "FIRE_SAFETY",
            "priority": 100,
        },
        {
            "question": "Можно ли тушить электроприбор водой?",
            "keywords": "электроприбор, электричество, вода, оборудование",
            "answer": "Нет. Сначала необходимо отключить электроприбор от сети. Воду нельзя использовать для тушения электрооборудования, находящегося под напряжением.",
            "variants": [
                "Нет. Сначала необходимо отключить электроприбор от сети. Воду нельзя использовать для тушения электрооборудования, находящегося под напряжением.",
                "Воду использовать нельзя, пока оборудование под напряжением. Сначала отключите его от сети, если это безопасно.",
            ],
            "category": "EQUIPMENT",
            "priority": 100,
        },
        {
            "question": "Как посмотреть прошлые публикации?",
            "keywords": "прошлые публикации, предыдущие посты, история канала",
            "answer": "Все предыдущие материалы можно найти в публикациях канала. 📚",
            "variants": [
                "Все предыдущие материалы можно найти в публикациях канала. 📚",
                "Откройте ленту канала и пролистайте её вверх — там собраны прошлые публикации. 📚",
            ],
            "category": "CHANNEL",
            "priority": 90,
        },
        {
            "question": "Когда следующий пост?",
            "keywords": "следующий пост, новая публикация, когда публикация",
            "answer": "Следите за каналом — новые публикации появляются регулярно. 👨‍🚒🔥",
            "variants": [
                "Следите за каналом — новые публикации появляются регулярно. 👨‍🚒🔥",
                "Следите за обновлениями канала — новый материал появится в ленте. 👨‍🚒",
            ],
            "category": "CHANNEL",
            "priority": 90,
        },
    ]
    created = 0
    for item in defaults:
        existing = await s.scalar(select(KnowledgeBase).where(
            KnowledgeBase.source_type == "faq",
            KnowledgeBase.question == item["question"],
            KnowledgeBase.chat_id.is_(None),
        ))
        if not existing:
            await add_faq_answer(s, **item)
            created += 1
    return created


async def save_post_knowledge(s, chat_id, source_message_id, content, title=None):
    if chat_id is None or source_message_id is None or not content:
        return None
    obj = await s.scalar(select(KnowledgeBase).where(
        KnowledgeBase.chat_id == chat_id,
        KnowledgeBase.source_message_id == source_message_id,
        KnowledgeBase.source_type == "post",
    ))
    if obj:
        obj.text = content.strip()
        if title:
            obj.title = title.strip()[:256]
        obj.active = True
        return obj
    return await add_knowledge(
        s,
        content,
        chat_id=chat_id,
        source_type="post",
        source_message_id=source_message_id,
        title=title,
    )


async def list_knowledge(s, limit=20):
    return (await s.scalars(select(KnowledgeBase).where(KnowledgeBase.active == True)
                            .order_by(desc(KnowledgeBase.id)).limit(limit))).all()


_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
_STOPWORDS = {
    "это", "как", "что", "кто", "где", "когда", "почему", "зачем", "можно",
    "ли", "для", "про", "или", "уже", "они", "она", "он", "этот", "того",
    "есть", "при", "под", "над", "так", "ещё", "еще",
}


def _knowledge_tokens(value):
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(value or "")
        if token.casefold() not in _STOPWORDS
    }


def _knowledge_score(query_tokens, item):
    content_tokens = _knowledge_tokens(
        f"{item.title or ''} {item.question or ''} {item.keywords or ''} {item.text}"
    )
    score = 0
    for query_token in query_tokens:
        if query_token in content_tokens:
            score += 3
        elif any(
            len(query_token) >= 4
            and len(content_token) >= 4
            and query_token[:4] == content_token[:4]
            for content_token in content_tokens
        ):
            score += 1
    return score


async def search_relevant_knowledge(s, query, chat_id, current_post_id=None, limit=3):
    query_tokens = _knowledge_tokens(query)
    if not query_tokens:
        return []

    channel_scope = or_(
        KnowledgeBase.chat_id == chat_id,
        and_(
            KnowledgeBase.chat_id.is_(None),
            KnowledgeBase.source_type == "manual",
        ),
    )
    conditions = [
        KnowledgeBase.active.is_(True),
        channel_scope,
    ]
    if current_post_id is not None:
        conditions.append(or_(
            KnowledgeBase.source_message_id.is_(None),
            KnowledgeBase.source_message_id != current_post_id,
        ))
    items = (await s.scalars(
        select(KnowledgeBase)
        .where(*conditions)
        .order_by(desc(KnowledgeBase.id))
    )).all()
    ranked = [
        (score, item)
        for item in items
        if (score := _knowledge_score(query_tokens, item)) > 0
    ]
    ranked.sort(key=lambda pair: (pair[0], pair[1].id), reverse=True)
    return [item for _, item in ranked[:max(0, limit)]]

async def delete_knowledge(s, item_id):
    obj = await s.get(KnowledgeBase, item_id)
    if obj: obj.active = False
    return obj


async def create_unanswered_question(
    s,
    chat_id,
    comment_id,
    question_text,
    post_context=None,
    failure_reason=None,
):
    existing = await s.scalar(select(UnansweredQuestion).where(
        UnansweredQuestion.comment_id == comment_id
    ))
    if existing:
        return existing
    obj = UnansweredQuestion(
        chat_id=chat_id,
        comment_id=comment_id,
        question_text=question_text.strip(),
        post_context=post_context,
        failure_reason=(failure_reason or "")[:128] or None,
    )
    s.add(obj)
    await s.flush()
    return obj


async def list_unanswered_questions(s, chat_id=None, limit=30, offset=0):
    conditions = [UnansweredQuestion.status.in_(["NEW", "PENDING"])]
    if chat_id is not None:
        conditions.append(UnansweredQuestion.chat_id == chat_id)
    return (await s.scalars(
        select(UnansweredQuestion).where(*conditions)
        .order_by(desc(UnansweredQuestion.updated_at), desc(UnansweredQuestion.id))
        .offset(max(0, offset))
        .limit(limit)
    )).all()


async def resolve_unanswered_question(s, question_id, status="RESOLVED"):
    obj = await s.get(UnansweredQuestion, question_id)
    if obj:
        obj.status = status
        obj.updated_at = datetime.utcnow()
    return obj


async def create_answer_candidate(
    s,
    chat_id,
    comment_id,
    question_text,
    answer_text,
    category="GENERAL",
    keywords=None,
    post_context=None,
):
    existing = await s.scalar(select(AnswerCandidate).where(
        AnswerCandidate.comment_id == comment_id
    ))
    if existing:
        return existing
    obj = AnswerCandidate(
        chat_id=chat_id,
        comment_id=comment_id,
        question_text=question_text.strip(),
        answer_text=answer_text.strip(),
        category=(category or "GENERAL").upper()[:32],
        keywords=(keywords or "").strip()[:2000] or None,
        post_context=post_context,
    )
    s.add(obj)
    await s.flush()
    return obj


async def list_answer_candidates(s, chat_id=None, status="PENDING", limit=30, offset=0):
    conditions = [AnswerCandidate.status == status]
    if chat_id is not None:
        conditions.append(AnswerCandidate.chat_id == chat_id)
    return (await s.scalars(
        select(AnswerCandidate).where(*conditions)
        .order_by(desc(AnswerCandidate.created_at), desc(AnswerCandidate.id))
        .offset(max(0, offset))
        .limit(limit)
    )).all()


async def approve_answer_candidate(s, candidate_id):
    candidate = await s.get(AnswerCandidate, candidate_id)
    if not candidate:
        return None
    if candidate.status == "APPROVED":
        return candidate
    if candidate.status != "PENDING":
        return candidate
    await add_faq_answer(
        s,
        candidate.question_text,
        candidate.keywords,
        candidate.answer_text,
        chat_id=candidate.chat_id,
        category=candidate.category,
        variants=[candidate.answer_text],
    )
    candidate.status = "APPROVED"
    candidate.updated_at = datetime.utcnow()
    return candidate


async def reject_answer_candidate(s, candidate_id):
    candidate = await s.get(AnswerCandidate, candidate_id)
    if not candidate:
        return None
    if candidate.status == "PENDING":
        candidate.status = "REJECTED"
        candidate.updated_at = datetime.utcnow()
    return candidate

async def stats(s):
    async def route_count(*routes):
        return await s.scalar(
            select(func.count(Comment.id)).where(Comment.route.in_(routes))
        ) or 0

    fast = await route_count("FAST_REACTION", "FAST")
    smart = await route_count("SMART")
    ai_answers = await route_count("AI")
    fallback = await route_count("FALLBACK")
    skipped = await route_count("FAST_REACTION", "MODERATION", "SKIPPED")
    ai_requests = await s.scalar(
        select(func.count(Comment.id)).where(Comment.ai_requested.is_(True))
    ) or 0
    ai_failures = await s.scalar(
        select(func.count(Comment.id)).where(Comment.ai_failed.is_(True))
    ) or 0
    unanswered = await s.scalar(
        select(func.count(UnansweredQuestion.id))
    ) or 0
    return {
        "comments": await s.scalar(select(func.count(Comment.id))) or 0,
        "ai": await s.scalar(select(func.count(AIAnalysis.id))) or 0,
        "replied": await s.scalar(select(func.count(AdminAction.id)).where(
            AdminAction.action.in_(["reply_variant","custom_reply","auto_reply"]))) or 0,
        "ignored": await s.scalar(select(func.count(AdminAction.id)).where(AdminAction.action == "ignore")) or 0,
        "deleted": await s.scalar(select(func.count(AdminAction.id)).where(AdminAction.action == "delete")) or 0,
        "fast": fast,
        "smart": smart,
        "ai_answers": ai_answers,
        "fallback": fallback,
        "skipped": skipped,
        "ai_requests": ai_requests,
        "ai_failures": ai_failures,
        "unanswered": unanswered,
        "ai_saved": fast + smart,
    }
