from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.config import get_settings
from app.database.database import get_session_factory
from app.database.models import Comment
from app.database.repositories import (
    get_settings as db_settings, stats, get_response, action, mark_processed,
    save_responses, add_knowledge, list_knowledge, delete_knowledge,
    save_published_response, add_faq_answer, list_faq_answers,
    list_answer_candidates, list_unanswered_questions,
    approve_answer_candidate, reject_answer_candidate,
)
from app.services.openai_service import OpenAIService
from app.bot.keyboards.admin import admin_keyboard
from app.bot.handlers.comments import process_comment
from app.performance import timed_command

router = Router()

def admin_only(uid): return uid in get_settings().admins

class CustomReply(StatesGroup):
    waiting_text = State()

@router.message(Command("settings"))
@timed_command("/settings")
async def settings(message: Message):
    if not admin_only(message.from_user.id): return
    async with get_session_factory()() as s:
        cfg = await db_settings(s); await s.commit()
    await message.answer(f"🤖 Автоответы: {'Включено' if cfg.auto_reply_enabled else 'Выключено'}\nПорог: {cfg.auto_reply_threshold:.0%}\n\nКоманды: /auto_on, /auto_off")

@router.message(Command("auto_on"))
@timed_command("/auto_on")
async def auto_on(message: Message):
    if not admin_only(message.from_user.id): return
    async with get_session_factory()() as s:
        cfg = await db_settings(s); cfg.auto_reply_enabled = True; await s.commit()
    await message.answer("🟢 Smart Auto включён. Бот отвечает только на безопасные комментарии с высокой уверенностью.")

@router.message(Command("auto_off"))
@timed_command("/auto_off")
async def auto_off(message: Message):
    if not admin_only(message.from_user.id): return
    async with get_session_factory()() as s:
        cfg = await db_settings(s); cfg.auto_reply_enabled = False; await s.commit()
    await message.answer("🟡 Автоответы выключены.")

@router.message(Command("stats"))
@timed_command("/stats")
async def statistics(message: Message):
    if not admin_only(message.from_user.id): return
    async with get_session_factory()() as s: x = await stats(s)
    await message.answer(
        f"📊 Статистика\n\n"
        f"💬 Всего комментариев: {x['comments']}\n"
        f"⚡ FAST/локально: {x['fast']}\n"
        f"🧠 SMART/FAQ: {x['smart']}\n"
        f"🤖 AI-анализов: {x['ai_requests']}\n"
        f"↩️ Fallback: {x['fallback']}\n"
        f"💾 AI-запросов сэкономлено: {x['ai_saved']}\n"
        f"⚠️ Ошибок AI: {x['ai_failures']}\n"
        f"❓ Без ответа: {x['unanswered']}\n"
        f"✍️ Опубликовано ответов: {x['replied']}\n"
        f"⏭ Пропущено: {x['skipped']}\n"
        f"🗑 Удалено: {x['deleted']}"
    )


@router.message(Command("answers"))
@timed_command("/answers")
async def answers(message: Message):
    if not admin_only(message.from_user.id):
        return
    async with get_session_factory()() as s:
        active = await list_faq_answers(s, limit=100)
        pending = await list_answer_candidates(s, status="PENDING", limit=100)
        unanswered = await list_unanswered_questions(s, limit=100)
    await message.answer(
        f"📚 Готовые ответы: {len(active)}\n"
        f"📝 Кандидаты на одобрение: {len(pending)}\n"
        f"❓ Вопросы без ответа: {len(unanswered)}"
    )


@router.message(Command("answer_candidates"))
@timed_command("/answer_candidates")
async def answer_candidates(message: Message):
    if not admin_only(message.from_user.id):
        return
    async with get_session_factory()() as s:
        items = await list_answer_candidates(s, status="PENDING")
    if not items:
        return await message.answer("📝 Кандидатов на одобрение пока нет.")
    text = "📝 <b>Кандидаты на одобрение</b>\n\n" + "\n\n".join(
        f"#{item.id}: <b>{escape(item.question_text[:240])}</b>\n"
        f"{escape(item.answer_text[:500])}\n"
        f"Категория: {escape(item.category)}"
        for item in items
    )
    await message.answer(text)


@router.message(Command("unanswered"))
@timed_command("/unanswered")
async def unanswered(message: Message):
    if not admin_only(message.from_user.id):
        return
    async with get_session_factory()() as s:
        items = await list_unanswered_questions(s)
    if not items:
        return await message.answer("❓ Вопросов без ответа нет.")
    text = "❓ <b>Вопросы без ответа</b>\n\n" + "\n\n".join(
        f"#{item.id} (комментарий #{item.comment_id})\n"
        f"{escape(item.question_text[:500])}\n"
        f"Причина: {escape(item.failure_reason or 'не указана')}"
        for item in items
    )
    await message.answer(text)


@router.message(Command("addanswer"))
@timed_command("/addanswer")
async def add_answer(message: Message):
    if not admin_only(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) != 3 or not all(parts):
        return await message.answer(
            "Использование:\n/addanswer вопрос | ключевые слова | ответ"
        )
    async with get_session_factory()() as s:
        item = await add_faq_answer(s, parts[0], parts[1], parts[2])
        await s.commit()
    await message.answer(f"✅ Готовый ответ сохранён: #{item.id}")


@router.message(Command("approveanswer"))
@timed_command("/approveanswer")
async def approve_answer(message: Message):
    if not admin_only(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit():
        return await message.answer("Использование: /approveanswer ID")
    async with get_session_factory()() as s:
        item = await approve_answer_candidate(s, int(raw))
        await s.commit()
    await message.answer(
        "✅ Кандидат одобрен и добавлен в готовые ответы."
        if item and item.status == "APPROVED"
        else "Кандидат не найден или уже обработан."
    )


@router.message(Command("rejectanswer"))
@timed_command("/rejectanswer")
async def reject_answer(message: Message):
    if not admin_only(message.from_user.id):
        return
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit():
        return await message.answer("Использование: /rejectanswer ID")
    async with get_session_factory()() as s:
        item = await reject_answer_candidate(s, int(raw))
        await s.commit()
    await message.answer(
        "🗑 Кандидат отклонён."
        if item and item.status == "REJECTED"
        else "Кандидат не найден или уже обработан."
    )

@router.message(Command("remember"))
@timed_command("/remember")
async def remember(message: Message):
    if not admin_only(message.from_user.id): return
    text = message.text.partition(" ")[2].strip()
    if not text: return await message.answer("Использование:\n/remember Текст знания")
    async with get_session_factory()() as s:
        item = await add_knowledge(s, text); await s.commit()
    await message.answer(f"🧠 Сохранено в базу знаний: #{item.id}")

@router.message(Command("knowledge"))
@timed_command("/knowledge")
async def knowledge(message: Message):
    if not admin_only(message.from_user.id): return
    async with get_session_factory()() as s: items = await list_knowledge(s)
    if not items: return await message.answer("🧠 База знаний пока пуста.")
    await message.answer(
        f"🧠 База знаний: {len(items)} записей\n\n" +
        "\n".join(
            f"#{x.id} [{x.source_type}] — {x.title or x.text}"
            for x in items
        )
    )

@router.message(Command("forget"))
@timed_command("/forget")
async def forget(message: Message):
    if not admin_only(message.from_user.id): return
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit(): return await message.answer("Использование: /forget ID")
    async with get_session_factory()() as s:
        item = await delete_knowledge(s, int(raw)); await s.commit()
    await message.answer("🗑 Запись отключена." if item else "Не найдено.")

@router.callback_query(F.data.startswith("reply:"))
async def reply(callback: CallbackQuery):
    if not admin_only(callback.from_user.id): return await callback.answer("Нет доступа", show_alert=True)
    _, cid, variant = callback.data.split(":"); cid, variant = int(cid), int(variant)
    async with get_session_factory()() as s:
        comment = await s.get(Comment, cid); response = await get_response(s, cid, variant)
        if not comment or not response: return await callback.answer("Данные не найдены", show_alert=True)
        if comment.processed: return await callback.answer("Комментарий уже обработан", show_alert=True)
        await callback.bot.send_message(comment.chat_id, response.text, reply_to_message_id=comment.telegram_message_id)
        await save_published_response(s, cid, response.text)
        await action(s, cid, callback.from_user.id, "reply_variant", variant); await mark_processed(s, cid); await s.commit()
    await callback.message.edit_reply_markup(reply_markup=None); await callback.message.answer("✅ Ответ опубликован."); await callback.answer()

@router.callback_query(F.data.startswith("ignore:"))
async def ignore(callback: CallbackQuery):
    if not admin_only(callback.from_user.id): return await callback.answer("Нет доступа", show_alert=True)
    cid = int(callback.data.split(":")[1])
    async with get_session_factory()() as s:
        comment = await s.get(Comment, cid)
        if not comment: return await callback.answer("Данные не найдены", show_alert=True)
        if comment.processed: return await callback.answer("Комментарий уже обработан", show_alert=True)
        await action(s, cid, callback.from_user.id, "ignore"); await mark_processed(s, cid); await s.commit()
    await callback.message.edit_reply_markup(reply_markup=None); await callback.message.answer("⏭ Комментарий оставлен без ответа."); await callback.answer()

@router.callback_query(F.data.startswith("delete:"))
async def delete(callback: CallbackQuery):
    if not admin_only(callback.from_user.id): return await callback.answer("Нет доступа", show_alert=True)
    cid = int(callback.data.split(":")[1])
    async with get_session_factory()() as s:
        comment = await s.get(Comment, cid)
        if not comment: return await callback.answer("Данные не найдены", show_alert=True)
        if comment.processed: return await callback.answer("Комментарий уже обработан", show_alert=True)
        try: await callback.bot.delete_message(comment.chat_id, comment.telegram_message_id)
        except Exception as e: return await callback.answer(f"Ошибка удаления: {e}", show_alert=True)
        await action(s, cid, callback.from_user.id, "delete"); await mark_processed(s, cid); await s.commit()
    await callback.message.edit_reply_markup(reply_markup=None); await callback.message.answer("🗑 Удалено."); await callback.answer()

@router.callback_query(F.data.startswith("reanalyze:"))
async def reanalyze(callback: CallbackQuery):
    if not admin_only(callback.from_user.id): return await callback.answer("Нет доступа", show_alert=True)
    cid = int(callback.data.split(":")[1])
    try:
        await process_comment(callback.bot, cid)
        await callback.answer("AI-анализ повторён.")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("regen:"))
async def regenerate(callback: CallbackQuery):
    if not admin_only(callback.from_user.id): return await callback.answer("Нет доступа", show_alert=True)
    cid = int(callback.data.split(":")[1])
    async with get_session_factory()() as s:
        comment = await s.get(Comment, cid)
        if comment and comment.processed:
            return await callback.answer("Комментарий уже обработан", show_alert=True)
        if not comment: return await callback.answer("Не найдено", show_alert=True)
        knowledge = [x.text for x in await list_knowledge(s)]
        settings = get_settings()
        ai = OpenAIService(settings.ai_api_key, settings.openai_model, settings.ai_base_url)
        responses = await ai.responses(comment.text, "DISCUSSION", knowledge)
        await save_responses(s, cid, responses); await s.commit()
    text = "🔄 Новые варианты:\n\n" + "\n\n".join(f"{r.variant}️⃣ {r.text}" for r in responses)
    await callback.message.edit_text(text, reply_markup=admin_keyboard(cid)); await callback.answer()

@router.callback_query(F.data.startswith("custom:"))
async def custom(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id): return await callback.answer("Нет доступа", show_alert=True)
    await state.update_data(comment_id=int(callback.data.split(":")[1])); await state.set_state(CustomReply.waiting_text)
    await callback.message.answer("✏️ Отправьте текст своего ответа следующим сообщением."); await callback.answer()

@router.message(CustomReply.waiting_text)
async def custom_text(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id) or not message.text: return
    cid = (await state.get_data())["comment_id"]
    async with get_session_factory()() as s:
        comment = await s.get(Comment, cid)
        await message.bot.send_message(comment.chat_id, message.text, reply_to_message_id=comment.telegram_message_id)
        await action(s, cid, message.from_user.id, "custom_reply"); await mark_processed(s, cid); await s.commit()
    await state.clear(); await message.answer("✅ Ваш ответ опубликован.")
