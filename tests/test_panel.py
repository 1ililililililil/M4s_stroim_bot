from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.bot.handlers import panel
from app.bot.keyboards.panel import panel_keyboard
from app.database.database import create_tables, dispose_db, get_session_factory, init_db
from app.database.models import AnswerCandidate, KnowledgeBase, UnansweredQuestion
from app.database.repositories import (
    add_faq_answer,
    create_answer_candidate,
    create_comment,
    create_unanswered_question,
    upsert_user,
)


class FakeMessage:
    def __init__(self, chat_id=500, message_id=10, text=""):
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = message_id
        self.text = text
        self.answers = []
        self.edits = []
        self.bot = self

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, **kwargs):
        self.edits.append(("markup", kwargs.get("reply_markup")))

    async def edit_message_text(self, **kwargs):
        self.edits.append((kwargs["text"], kwargs.get("reply_markup")))


class FakeCallback:
    def __init__(self, data, user_id=1, message=None):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = message or FakeMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}
        self.current = None
        self.cleared = False

    async def clear(self):
        self.data = {}
        self.cleared = True

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.current = state


@pytest_asyncio.fixture
async def panel_db(tmp_path, monkeypatch):
    init_db(f"sqlite+aiosqlite:///{tmp_path / 'panel.db'}")
    await create_tables()
    monkeypatch.setattr(
        panel,
        "get_settings",
        lambda: SimpleNamespace(
            admins={1},
            ai_api_key="test-key",
            openai_model="test-model",
            ai_base_url=None,
        ),
    )
    yield
    await dispose_db()


@pytest.mark.asyncio
async def test_panel_command_opens_main_inline_panel(monkeypatch):
    monkeypatch.setattr(panel, "get_settings", lambda: SimpleNamespace(admins={1}))
    message = FakeMessage()
    message.from_user = SimpleNamespace(id=1)

    await panel.panel(message)

    assert "МЧС | Мы Чего-то Строим" in message.answers[0][0]
    callback_values = [
        button.callback_data
        for row in message.answers[0][1].inline_keyboard
        for button in row
    ]
    assert "p:auto" in callback_values
    assert "p:kb" in callback_values
    assert "p:uq" in callback_values
    assert "p:candidates" in callback_values


@pytest.mark.asyncio
async def test_non_admin_cannot_use_panel_callback(monkeypatch):
    monkeypatch.setattr(panel, "get_settings", lambda: SimpleNamespace(admins={1}))
    callback = FakeCallback("p:stats", user_id=99)

    await panel.panel_stats(callback)

    assert callback.answers == [("Нет доступа", True)]
    assert callback.message.edits == []


@pytest.mark.asyncio
async def test_back_button_returns_to_main_panel(monkeypatch):
    monkeypatch.setattr(panel, "get_settings", lambda: SimpleNamespace(admins={1}))
    callback = FakeCallback("p:home")

    await panel.home(callback)

    assert "Панель управления ботом" in callback.message.edits[0][0]
    assert callback.answers == [(None, False)]


@pytest.mark.asyncio
async def test_faq_save_rejects_incomplete_draft(panel_db):
    callback = FakeCallback("p:kb:save")
    state = FakeState({"question": "Только вопрос"})

    await panel.faq_save(callback, state)

    assert state.cleared is True
    assert callback.answers[0][1] is True
    async with get_session_factory()() as session:
        assert (await session.scalars(select(UnansweredQuestion))).all() == []


@pytest.mark.asyncio
async def test_faq_save_persists_complete_draft(panel_db):
    callback = FakeCallback("p:kb:save")
    state = FakeState({
        "question": "Как вызвать пожарных?",
        "keywords": "101, 112",
        "answer": "Позвоните 101 или 112.",
        "variants": ["Сообщите о пожаре по номеру 101."],
    })

    await panel.faq_save(callback, state)

    assert state.cleared is True
    async with get_session_factory()() as session:
        answers = (await session.scalars(
            select(UnansweredQuestion)
        )).all()
        assert answers == []
        faq = await session.scalar(select(KnowledgeBase))
        assert faq.question == "Как вызвать пожарных?"


@pytest.mark.asyncio
async def test_unanswered_resolution_does_not_create_duplicate(panel_db):
    async with get_session_factory()() as session:
        user = await upsert_user(session, 55, "user", "User")
        comment = await create_comment(
            session,
            telegram_message_id=55,
            chat_id=-55,
            user_id=user.id,
            text="Неизвестный вопрос",
        )
        await create_unanswered_question(
            session,
            -55,
            comment.id,
            comment.text,
            failure_reason="AI",
        )
        await session.commit()
        question_id = comment.id

    callback = FakeCallback(f"p:uq:close:{question_id}")
    await panel.unanswered_close(callback)

    async with get_session_factory()() as session:
        items = (await session.scalars(select(UnansweredQuestion))).all()
        assert len(items) == 1
        assert items[0].status == "RESOLVED"


@pytest.mark.asyncio
async def test_candidate_buttons_reuse_approval_and_rejection(panel_db):
    async with get_session_factory()() as session:
        user = await upsert_user(session, 77, "user", "User")
        comment = await create_comment(
            session,
            telegram_message_id=77,
            chat_id=-77,
            user_id=user.id,
            text="Вопрос кандидата",
        )
        approved = await create_answer_candidate(
            session, -77, comment.id, comment.text, "Готовый ответ"
        )
        second_comment = await create_comment(
            session,
            telegram_message_id=78,
            chat_id=-77,
            user_id=user.id,
            text="Второй вопрос",
        )
        rejected = await create_answer_candidate(
            session, -77, second_comment.id, second_comment.text, "Другой ответ"
        )
        await session.commit()

    await panel.candidate_approve(FakeCallback(f"p:ca:{approved.id}"))
    await panel.candidate_reject(FakeCallback(f"p:cr:{rejected.id}"))

    async with get_session_factory()() as session:
        saved = await session.get(AnswerCandidate, approved.id)
        refused = await session.get(AnswerCandidate, rejected.id)
        assert saved.status == "APPROVED"
        assert refused.status == "REJECTED"