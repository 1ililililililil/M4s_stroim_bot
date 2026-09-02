import pytest
from pathlib import Path
from app.database.database import init_db, create_tables, get_session_factory, dispose_db
from app.database.repositories import (
    upsert_user, create_comment, claim_processing, claim_auto_reply,
    mark_processed, save_published_response,
)

@pytest.mark.asyncio
async def test_database():
    path = Path("data/test.db")
    path.parent.mkdir(exist_ok=True)
    if path.exists(): path.unlink()
    init_db("sqlite+aiosqlite:///./data/test.db")
    await create_tables()
    async with get_session_factory()() as s:
        u = await upsert_user(s, 1, "tester", "Tester")
        c = await create_comment(
            s,
            telegram_message_id=1,
            chat_id=-100,
            user_id=u.id,
            text="hello",
            post_text="Original post",
            reply_to_message_id=None,
        )
        await s.commit()
        assert c.id
        assert await claim_processing(s, c.id)
        await s.commit()
        assert await claim_auto_reply(s, c.id)
        await save_published_response(s, c.id, "reply")
        await mark_processed(s, c.id)
        await s.commit()
        assert c.auto_reply_sent is True
        assert c.published_response == "reply"
        assert c.processed is True
    await dispose_db()
