# updated app/main.py
import asyncio, logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI
import uvicorn
from app.config import get_settings
from app.database.database import init_db, create_tables, dispose_db, get_session_factory
from app.database.repositories import seed_default_faqs
from app.bot.handlers import start, comments, admin, panel
from app.services.openai_service import init_global_service, close_global_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
health_app = FastAPI()

@health_app.get("/health")
async def health():
    return {"status":"ok", "version":"3"}

async def run_health(port):
    server = uvicorn.Server(uvicorn.Config(health_app, host="0.0.0.0", port=port, log_level="warning"))
    await server.serve()

async def main():
    settings = get_settings()
    # initialize DB
    init_db(settings.database_url)
    await create_tables()

    # seed default faqs
    async with get_session_factory()() as session:
        seeded = await seed_default_faqs(session)
        await session.commit()
    if seeded:
        logging.info("Seeded local FAQ answers count=%s", seeded)

    # initialize global AI client if api key present
    try:
        if settings.ai_api_key:
            await init_global_service(settings.ai_api_key, settings.openai_model, settings.ai_base_url)
    except Exception:
        logging.exception("Failed to initialize global AI service")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(panel.router)
    dp.include_router(comments.router)

    logging.info("MCHS AI Admin Bot V3 запущен. Админов: %s", len(settings.admins))
    await comments.start_comment_queue()
    try:
        await asyncio.gather(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            run_health(settings.health_port),
        )
    finally:
        # stop processing queue and close resources
        await comments.stop_comment_queue()
        try:
            await close_global_service()
        except Exception:
            logging.exception("Error closing global AI service")
        await bot.session.close()
        await dispose_db()

if __name__ == "__main__":
    asyncio.run(main())
