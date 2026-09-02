from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.bot.handlers import comments
from app.database.database import create_tables, dispose_db, get_session_factory, init_db
from app.database.models import AnswerCandidate, Comment, UnansweredQuestion
from app.database.repositories import (
    add_faq_answer,
    approve_answer_candidate,
    create_comment,
    create_answer_candidate,
    create_unanswered_question,
    upsert_user,
)
from app.schemas.ai import AIAnalysisResult, ResponseVariant


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class FakeAI:
    def __init__(self, error=None):
        self.error = error
        self.analysis_calls = 0
        self.response_calls = 0

    async def analyze(self, text, knowledge=None, post_context=None):
        self.analysis_calls += 1
        if self.error:
            raise self.error
        return AIAnalysisResult(
            category="QUESTION",
            confidence=0.95,
            requires_admin=False,
            should_reply=True,
        )

    async def analyze_with_reply(self, text, knowledge=None, post_context=None):
        self.analysis_calls += 1
        if self.error:
            raise self.error
        return AIAnalysisResult(
            category="QUESTION",
            confidence=0.95,
            requires_admin=False,
            should_reply=True,
        ).model_copy(update={"reply": "AI ответ"})

    async def responses(self, comment, category, knowledge=None, post_context=None):
        self.response_calls += 1
        if self.error:
            raise self.error
        return [ResponseVariant(variant=1, text="AI ответ")]


@pytest_asyncio.fixture
async def isolated_db(tmp_path):
    init_db(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await create_tables()
    yield
    await dispose_db()


def patch_runtime(monkeypatch, fake_ai, auto_reply_enabled=False):
    monkeypatch.setattr(
        comments,
        "get_settings",
        lambda: SimpleNamespace(
            admins={999},
            ai_api_key="test-key",
            openai_model="test-model",
            ai_base_url=None,
        ),
    )
    monkeypatch.setattr(comments, "OpenAIService", lambda *args: fake_ai)


async def seed_comment(text, telegram_message_id=100):
    async with get_session_factory()() as session:
        user = await upsert_user(session, 42, "tester", "Tester")
        comment = await create_comment(
            session,
            telegram_message_id=telegram_message_id,
            chat_id=-1001234567890,
            user_id=user.id,
            post_text="Публикация канала",
            text=text,
            reply_to_message_id=99,
        )
        await session.commit()
        return comment.id


async def enable_auto_reply():
    async with get_session_factory()() as session:
        settings = await comments.db_settings(session)
        settings.auto_reply_enabled = True
        settings.auto_reply_threshold = 0.8
        await session.commit()


async def seed_faq(question, keywords, answer):
    async with get_session_factory()() as session:
        item = await add_faq_answer(session, question, keywords, answer)
        await session.commit()
        return item.id


@pytest.mark.asyncio
async def test_exact_local_answer_skips_ai_and_uses_variants(
    isolated_db, monkeypatch
):
    await seed_faq(
        "Как вызвать пожарных?",
        "101, 112, вызвать пожарных",
        "Позвоните 101 или 112.",
    )
    fake_ai = FakeAI(error=AssertionError("FAST must not call AI"))
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Как вызвать пожарных?")

    assert await comments.process_comment(FakeBot(), comment_id, notify=False) is True
    assert fake_ai.analysis_calls == 0
    async with get_session_factory()() as session:
        comment = await session.get(Comment, comment_id)
        assert comment.route == "FAST"
        assert comment.ai_requested is False


@pytest.mark.asyncio
async def test_smart_paraphrase_skips_ai(isolated_db, monkeypatch):
    await seed_faq(
        "Что делать при пожаре?",
        "пожар, огонь, эвакуация",
        "Позвоните 101 или 112 и покиньте опасную зону.",
    )
    fake_ai = FakeAI(error=AssertionError("SMART must not call AI"))
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Что делать если начался пожар?")

    assert await comments.process_comment(FakeBot(), comment_id, notify=False) is True
    assert fake_ai.analysis_calls == 0
    async with get_session_factory()() as session:
        comment = await session.get(Comment, comment_id)
        assert comment.route == "SMART"


@pytest.mark.asyncio
async def test_ai_failure_uses_fresh_local_fallback(
    isolated_db, monkeypatch
):
    fake_ai = FakeAI(error=RuntimeError("provider unavailable"))
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Что делать если начался пожар?")

    original_search = comments.search_faq_answers
    await seed_faq(
        "Что делать при пожаре?",
        "пожар, огонь, эвакуация",
        "Позвоните 101 или 112 и покиньте опасную зону.",
    )
    calls = 0

    async def delayed_faq_search(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return await original_search(*args, **kwargs)

    monkeypatch.setattr(comments, "search_faq_answers", delayed_faq_search)
    await enable_auto_reply()
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is True
    assert fake_ai.analysis_calls == 1
    assert len(bot.sent) == 1
    assert "101" in bot.sent[0][1]
    async with get_session_factory()() as session:
        comment = await session.get(Comment, comment_id)
        assert comment.route == "FALLBACK"
        assert comment.ai_failed is True
        assert comment.fallback_used is True


@pytest.mark.asyncio
async def test_ai_failure_records_one_unanswered_question(
    isolated_db, monkeypatch
):
    fake_ai = FakeAI(error=RuntimeError("provider unavailable"))
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Как устроиться на работу?")
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is False
    assert await comments.process_comment(bot, comment_id, notify=False) is False
    async with get_session_factory()() as session:
        items = (await session.scalars(select(UnansweredQuestion))).all()
        comment = await session.get(Comment, comment_id)
        assert len(items) == 1
        assert comment.route == "UNANSWERED"
        assert comment.processed is False


@pytest.mark.asyncio
async def test_candidate_requires_approval_before_local_use(
    isolated_db, monkeypatch
):
    fake_ai = FakeAI()
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Сколько длится подготовка пожарного?")

    assert await comments.process_comment(FakeBot(), comment_id, notify=False) is True
    async with get_session_factory()() as session:
        candidate = await session.scalar(select(AnswerCandidate))
        assert candidate.status == "PENDING"
        await approve_answer_candidate(session, candidate.id)
        await session.commit()

    fake_ai.analysis_calls = 0
    second_id = await seed_comment(
        "Сколько длится подготовка пожарного?",
        telegram_message_id=101,
    )
    assert await comments.process_comment(FakeBot(), second_id, notify=False) is True
    assert fake_ai.analysis_calls == 0


@pytest.mark.asyncio
async def test_unanswered_creation_is_idempotent(isolated_db):
    async with get_session_factory()() as session:
        user = await upsert_user(session, 7, "tester", "Tester")
        comment = await create_comment(
            session,
            telegram_message_id=101,
            chat_id=-100,
            user_id=user.id,
            text="Неизвестный вопрос?",
        )
        first = await create_unanswered_question(
            session, -100, comment.id, comment.text, failure_reason="test"
        )
        second = await create_unanswered_question(
            session, -100, comment.id, comment.text, failure_reason="test"
        )
        await session.commit()
        assert first.id == second.id