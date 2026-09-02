import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.bot.handlers import comments
from app.database.database import dispose_db, get_session_factory, init_db, create_tables
from app.database.models import Comment
from app.database.repositories import create_comment, get_settings, upsert_user
from app.schemas.ai import AIAnalysisResult, ResponseVariant


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class FakeAI:
    def __init__(self, result=None, error=None):
        self.result = result or AIAnalysisResult(
            category="QUESTION",
            confidence=1.0,
            requires_admin=False,
            should_reply=True,
        )
        self.error = error
        self.delay = 0
        self.analysis_calls = 0
        self.analysis_contexts = []
        self.response_contexts = []

    async def analyze(self, text, knowledge=None, post_context=None):
        self.analysis_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.analysis_contexts.append(post_context)
        return self.result

    async def analyze_with_reply(self, text, knowledge=None, post_context=None):
        self.analysis_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.analysis_contexts.append(post_context)
        return self.result.model_copy(update={"reply": "Короткий ответ"})

    async def responses(self, comment, category, knowledge=None, post_context=None):
        self.response_contexts.append(post_context)
        return [ResponseVariant(variant=1, text="Короткий ответ")]


async def seed_comment(text, post_text=None):
    Session = get_session_factory()
    async with Session() as session:
        user = await upsert_user(session, 42, "tester", "Tester")
        comment = await create_comment(
            session,
            telegram_message_id=100,
            chat_id=-1001234567890,
            user_id=user.id,
            post_text=post_text,
            text=text,
            reply_to_message_id=99,
        )
        await session.commit()
        return comment.id


@pytest_asyncio.fixture
async def isolated_db(tmp_path):
    init_db(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await create_tables()
    yield
    await dispose_db()


def patch_runtime(monkeypatch, fake_ai, auto_reply_enabled=True):
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


async def enable_auto_reply(threshold=0.9):
    async with get_session_factory()() as session:
        cfg = await get_settings(session)
        cfg.auto_reply_enabled = True
        cfg.auto_reply_threshold = threshold
        await session.commit()


@pytest.mark.asyncio
async def test_question_uses_post_context_and_publishes_once(
    isolated_db, monkeypatch
):
    fake_ai = FakeAI()
    patch_runtime(monkeypatch, fake_ai)
    await enable_auto_reply()

    comment_id = await seed_comment("А сколько длится выезд?", "Будни пожарного")
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is True
    assert await comments.process_comment(bot, comment_id, notify=False) is False
    assert fake_ai.analysis_contexts == ["Будни пожарного"]
    assert fake_ai.response_contexts == []
    assert len(bot.sent) == 1

    Session = get_session_factory()
    async with Session() as session:
        saved = await session.get(Comment, comment_id)
        assert saved.processed is True
        assert saved.auto_reply_sent is True
        assert saved.published_response == "Короткий ответ"


@pytest.mark.asyncio
async def test_reaction_is_processed_without_reply(isolated_db, monkeypatch):
    fake_ai = FakeAI(
        AIAnalysisResult(
            category="PRAISE",
            confidence=1.0,
            requires_admin=True,
            should_reply=True,
        )
    )
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("🔥🔥🔥")
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is True
    assert fake_ai.analysis_contexts == []
    assert bot.sent == []

    async with get_session_factory()() as session:
        saved = await session.get(Comment, comment_id)
        assert saved.processed is True


@pytest.mark.asyncio
async def test_meaningful_praise_gets_local_reply_without_ai(
    isolated_db, monkeypatch
):
    fake_ai = FakeAI(error=AssertionError("meaningful praise must stay local"))
    patch_runtime(monkeypatch, fake_ai)
    await enable_auto_reply()
    comment_id = await seed_comment("Класс, очень интересно!")
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is True
    assert fake_ai.analysis_calls == 0
    assert len(bot.sent) == 1
    assert "Спасибо" in bot.sent[0][1] or "Благодарю" in bot.sent[0][1] or "приятно" in bot.sent[0][1]

    async with get_session_factory()() as session:
        saved = await session.get(Comment, comment_id)
        assert saved.route == "FAST"
        assert saved.ai_requested is False
        assert saved.processed is True
        assert saved.auto_reply_sent is True


@pytest.mark.asyncio
async def test_meaningful_discussion_gets_local_reply_without_ai(
    isolated_db, monkeypatch
):
    fake_ai = FakeAI(error=AssertionError("meaningful discussion must stay local"))
    patch_runtime(monkeypatch, fake_ai)
    await enable_auto_reply()
    comment_id = await seed_comment(
        "Не совпало, но близко, училась на секретаря, работаю бухгалтером"
    )
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is True
    assert fake_ai.analysis_calls == 0
    assert len(bot.sent) == 1

    async with get_session_factory()() as session:
        saved = await session.get(Comment, comment_id)
        assert saved.route == "SMART"
        assert saved.ai_requested is False
        assert saved.processed is True
        assert saved.auto_reply_sent is True


@pytest.mark.asyncio
async def test_spam_goes_to_moderation_without_ai_response(isolated_db, monkeypatch):
    fake_ai = FakeAI(error=AssertionError("AI must not analyze prechecked spam"))
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Реклама, купить здесь https://example.com")
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id) is True
    assert bot.sent and "Требуется модерация" in bot.sent[0][1]
    assert fake_ai.analysis_contexts == []

    async with get_session_factory()() as session:
        saved = await session.get(Comment, comment_id)
        assert saved.processed is False


@pytest.mark.asyncio
async def test_duplicate_queue_entries_are_processed_once(isolated_db, monkeypatch):
    fake_ai = FakeAI()
    fake_ai.delay = 0.05
    patch_runtime(monkeypatch, fake_ai)
    await enable_auto_reply()
    comment_id = await seed_comment("Класс! А как это работает?")
    bot = FakeBot()

    await comments.start_comment_queue()
    try:
        await asyncio.gather(
            comments.enqueue_comment(bot, comment_id),
            comments.enqueue_comment(bot, comment_id),
        )
        await comments._comment_queue._queue.join()
    finally:
        await comments.stop_comment_queue()

    assert fake_ai.analysis_calls == 1
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_parallel_processing_has_single_claim(isolated_db, monkeypatch):
    fake_ai = FakeAI()
    fake_ai.delay = 0.05
    patch_runtime(monkeypatch, fake_ai)
    await enable_auto_reply()
    comment_id = await seed_comment("Спасибо, а что делать при пожаре?")
    bot = FakeBot()

    results = await asyncio.gather(
        comments.process_comment(bot, comment_id, notify=False),
        comments.process_comment(bot, comment_id, notify=False),
    )

    assert sorted(results) == [False, True]
    assert fake_ai.analysis_calls == 1
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_ai_error_is_recorded_without_empty_reply(isolated_db, monkeypatch):
    fake_ai = FakeAI(error=RuntimeError("AI unavailable"))
    patch_runtime(monkeypatch, fake_ai)
    comment_id = await seed_comment("Расскажите подробнее?")
    bot = FakeBot()

    assert await comments.process_comment(bot, comment_id, notify=False) is False
    assert bot.sent == []
    assert await comments.process_comment(bot, comment_id, notify=False) is False
    assert fake_ai.analysis_calls == 1

    async with get_session_factory()() as session:
        saved = await session.get(Comment, comment_id)
        assert saved.processed is False
        assert saved.processing is False
        assert "AI unavailable" in saved.last_error