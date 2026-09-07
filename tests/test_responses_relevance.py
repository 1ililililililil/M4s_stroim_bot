import pytest
import asyncio
from app.services.openai_service import OpenAIService


@pytest.mark.asyncio
async def test_responses_relevance_negative(monkeypatch):
    svc = OpenAIService(api_key="x", model="test")

    async def fake_json(prompt: str):
        # AI returns answers about profession/training — irrelevant to the event comment
        return {
            "responses": [
                {"variant": 1, "text": "Иногда профессия меняется, а навыки всё равно остаются с нами."},
                {"variant": 2, "text": "Профессия — это путь, который формируют годы обучения."},
                {"variant": 3, "text": "Важно развиваться и осваивать новые навыки в профессии."},
            ]
        }

    monkeypatch.setattr(svc, "_json", fake_json)

    comment = "Люблю ходить на так��е мероприятия, но, к сожалению, не всегда получается("
    with pytest.raises(ValueError):
        await svc.responses(comment, "DISCUSSION", knowledge=None, post_context="Пост о мероприятии")


@pytest.mark.asyncio
async def test_responses_relevance_positive(monkeypatch):
    svc = OpenAIService(api_key="x", model="test")

    async def fake_json(prompt: str):
        # AI returns three relevant variations addressing inability to attend
        return {
            "responses": [
                {"variant": 1, "text": "Понимаю 😊 Иногда очень хочется выбраться, но обстоятельства берут своё. Надеюсь, в следующий раз получится 👍"},
                {"variant": 2, "text": "Да, такое бывает 😄 Не всегда удаётся посетить мероприятия, зато ценишь их больше, когда получается."},
                {"variant": 3, "text": "Главное, что желание есть — надеемся, что в следующий раз вы сможете прийти и насладиться событием."},
            ]
        }

    monkeypatch.setattr(svc, "_json", fake_json)

    comment = "Люблю ходить на такие мероприятия, но, к сожалению, не всегда получается("
    res = await svc.responses(comment, "DISCUSSION", knowledge=None, post_context="Пост о мероприятии")
    assert len(res) == 3
    texts = [r.text for r in res]
    assert any("Понимаю" in t or "надеюсь" in t or "удаётся" in t or "в следующий" in t for t in texts)
