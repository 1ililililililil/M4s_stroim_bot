import json
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.bot.keyboards.panel import (
    auto_keyboard,
    back_keyboard,
    cancel_keyboard,
    candidate_list_keyboard,
    faq_list_keyboard,
    faq_preview_keyboard,
    knowledge_keyboard,
    panel_keyboard,
    settings_keyboard,
    unanswered_detail_keyboard,
    unanswered_list_keyboard,
)
from app.config import get_settings
from app.database.database import get_session_factory
from app.database.models import AnswerCandidate, Comment, KnowledgeBase, UnansweredQuestion
from app.database.repositories import (
    add_faq_answer,
    approve_answer_candidate,
    create_answer_candidate,
    get_settings as get_db_settings,
    list_answer_candidates,
    list_faq_answers,
    list_knowledge,
    list_unanswered_questions,
    reject_answer_candidate,
    resolve_unanswered_question,
    search_faq_answers,
    stats,
)
from app.performance import timed_command
from app.services.openai_service import OpenAIService


router = Router()
PAGE_SIZE = 5


class FAQDraft(StatesGroup):
    question = State()
    keywords = State()
    answer = State()
    variants = State()
    preview = State()


class FAQSearch(StatesGroup):
    query = State()


class UnansweredDraft(StatesGroup):
    answer = State()


def is_admin(user_id: int) -> bool:
    return user_id in get_settings().admins


async def _safe_edit(message, text, reply_markup=None):
    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return None
        raise


async def _edit_saved_panel(message: Message, data: dict, text: str, reply_markup=None):
    try:
        await message.bot.edit_message_text(
            chat_id=data["panel_chat_id"],
            message_id=data["panel_message_id"],
            text=text,
            reply_markup=reply_markup,
        )
    except Exception:
        await message.answer(text, reply_markup=reply_markup)


async def render_home(target, edit=False):
    text = (
        "👨‍🚒 <b>МЧС | Мы Чего-то Строим</b>\n"
        "Панель управления ботом"
    )
    if edit:
        await _safe_edit(target.message, text, panel_keyboard())
    else:
        await target.answer(text, reply_markup=panel_keyboard())


async def _stats_text():
    async with get_session_factory()() as session:
        values = await stats(session)
    return (
        "📊 <b>Статистика</b>\n\n"
        f"⚡ FAST обработано: <b>{values['fast']}</b>\n"
        f"🧠 SMART/FAQ ответы: <b>{values['smart']}</b>\n"
        f"🤖 AI запросы: <b>{values['ai_requests']}</b>\n"
        f"🟠 FALLBACK ответы: <b>{values['fallback']}</b>\n"
        f"💰 AI запросов сэкономлено: <b>{values['ai_saved']}</b>\n"
        f"⚠️ Ошибки AI: <b>{values['ai_failures']}</b>\n"
        f"❓ Неотвеченные вопросы: <b>{values['unanswered']}</b>\n"
        f"📤 Опубликованные ответы: <b>{values['replied']}</b>\n"
        f"⏭ Пропущенные комментарии: <b>{values['skipped']}</b>\n"
        f"🚫 Удалённые комментарии: <b>{values['deleted']}</b>"
    )


async def _render_stats(callback: CallbackQuery, answer=True):
    await _safe_edit(
        callback.message,
        await _stats_text(),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="p:stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:home")],
        ]),
    )
    if answer:
        await callback.answer()


async def _render_auto(callback: CallbackQuery, answer=True):
    async with get_session_factory()() as session:
        config = await get_db_settings(session)
    status = "🟢 включены" if config.auto_reply_enabled else "🔴 выключены"
    text = (
        "🤖 <b>Автоответы</b>\n\n"
        f"Smart Auto: <b>{status}</b>\n"
        f"Порог уверенности: <b>{config.auto_reply_threshold:.0%}</b>\n\n"
        "Маршрутизация:\n"
        "FAST → SMART → AI → FALLBACK\n\n"
        "FAST и SMART отвечают локально. AI используется только для "
        "неразрешённых сложных вопросов."
    )
    await _safe_edit(callback.message, text, auto_keyboard(config.auto_reply_enabled))
    if answer:
        await callback.answer()


async def _render_settings(callback: CallbackQuery, answer=True):
    async with get_session_factory()() as session:
        config = await get_db_settings(session)
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"🤖 Автоответы: {'🟢 включены' if config.auto_reply_enabled else '🔴 выключены'}\n"
        f"🎯 Порог уверенности: <b>{config.auto_reply_threshold:.0%}</b>\n"
        "🧠 Маршрут: <b>FAST → SMART → AI → FALLBACK</b>\n\n"
        "Секреты, модель и API-конфигурация здесь не отображаются и не изменяются."
    )
    await _safe_edit(callback.message, text, settings_keyboard())
    if answer:
        await callback.answer()


def _variant_count(item) -> int:
    try:
        values = json.loads(item.answer_variants or "[]")
        return len(values) if isinstance(values, list) else 1
    except (TypeError, ValueError):
        return 1


async def _render_knowledge(callback: CallbackQuery, answer=True):
    async with get_session_factory()() as session:
        total = await session.scalar(
            select(func.count(KnowledgeBase.id)).where(KnowledgeBase.active.is_(True))
        ) or 0
        faq = await list_faq_answers(session, limit=200)
    variants = sum(_variant_count(item) for item in faq)
    text = (
        "🧠 <b>База знаний</b>\n\n"
        f"Всего активных записей: <b>{total}</b>\n"
        f"Активных FAQ: <b>{len(faq)}</b>\n"
        f"Доступных вариантов ответов: <b>{variants}</b>"
    )
    await _safe_edit(callback.message, text, knowledge_keyboard())
    if answer:
        await callback.answer()


async def _render_faq_list(callback: CallbackQuery, page: int, answer=True):
    page = max(0, page)
    async with get_session_factory()() as session:
        entries = await list_faq_answers(
            session,
            limit=PAGE_SIZE + 1,
            offset=page * PAGE_SIZE,
        )
    has_next = len(entries) > PAGE_SIZE
    entries = entries[:PAGE_SIZE]
    if not entries:
        text = "📚 <b>Список FAQ</b>\n\nНа этой странице записей нет."
    else:
        lines = [f"📚 <b>Список FAQ · страница {page + 1}</b>\n"]
        for item in entries:
            question = item.question or item.title or item.text
            lines.append(
                f"<b>#{item.id}</b> {escape(question[:220])}\n"
                f"{escape(item.text[:280])}\n"
                f"Вариантов: {_variant_count(item)}"
            )
        text = "\n\n".join(lines)
    await _safe_edit(callback.message, text, faq_list_keyboard(page, has_next))
    if answer:
        await callback.answer()


async def _render_unanswered_list(callback: CallbackQuery, page: int, answer=True):
    page = max(0, page)
    async with get_session_factory()() as session:
        items = await list_unanswered_questions(
            session,
            limit=PAGE_SIZE + 1,
            offset=page * PAGE_SIZE,
        )
    has_next = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    rows = []
    if not items:
        text = "❓ <b>Неотвеченные вопросы</b>\n\nСписок пуст."
    else:
        lines = [f"❓ <b>Неотвеченные вопросы · страница {page + 1}</b>\n"]
        for item in items:
            lines.append(
                f"<b>#{item.id}</b> {escape(item.question_text[:240])}\n"
                f"Статус: {escape(item.status)}"
            )
            rows.append([
                InlineKeyboardButton(
                    text=f"Открыть #{item.id}",
                    callback_data=f"p:uq:item:{item.id}",
                )
            ])
        text = "\n\n".join(lines)
    keyboard = unanswered_list_keyboard(page, has_next)
    keyboard.inline_keyboard = rows + keyboard.inline_keyboard
    await _safe_edit(callback.message, text, keyboard)
    if answer:
        await callback.answer()


async def _render_unanswered_detail(callback: CallbackQuery, question_id: int, answer=True):
    async with get_session_factory()() as session:
        item = await session.get(UnansweredQuestion, question_id)
    if not item:
        return await callback.answer("Вопрос не найден или уже закрыт.", show_alert=True)
    context = f"\n📄 Контекст: {escape(item.post_context[:500])}" if item.post_context else ""
    text = (
        f"❓ <b>Вопрос #{item.id}</b>\n\n"
        f"{escape(item.question_text[:1500])}{context}\n\n"
        f"⚠️ Статус: <b>{escape(item.status)}</b>\n"
        f"Причина: {escape(item.failure_reason or 'не указана')}"
    )
    await _safe_edit(callback.message, text, unanswered_detail_keyboard(item.id))
    if answer:
        await callback.answer()


async def _render_candidates(callback: CallbackQuery, page: int, answer=True):
    page = max(0, page)
    async with get_session_factory()() as session:
        items = await list_answer_candidates(
            session,
            status="PENDING",
            limit=PAGE_SIZE + 1,
            offset=page * PAGE_SIZE,
        )
    has_next = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    if not items:
        text = "💡 <b>Кандидаты</b>\n\nОжидающих одобрения кандидатов нет."
    else:
        lines = [f"💡 <b>Кандидаты · страница {page + 1}</b>\n"]
        for item in items:
            lines.append(
                f"<b>#{item.id}</b>\n"
                f"❓ {escape(item.question_text[:260])}\n"
                f"🤖 {escape(item.answer_text[:500])}"
            )
        text = "\n\n".join(lines)
    await _safe_edit(
        callback.message,
        text,
        candidate_list_keyboard(page, has_next, [item.id for item in items]),
    )
    if answer:
        await callback.answer()


async def _set_auto(enabled: bool):
    async with get_session_factory()() as session:
        config = await get_db_settings(session)
        config.auto_reply_enabled = enabled
        await session.commit()


@router.message(Command("panel"))
@timed_command("/panel")
async def panel(message: Message):
    if is_admin(message.from_user.id):
        await render_home(message)


@router.callback_query(F.data == "p:home")
async def home(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await render_home(callback, edit=True)
    await callback.answer()


@router.callback_query(F.data == "p:refresh")
async def refresh(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await render_home(callback, edit=True)
    await callback.answer("Панель обновлена")


@router.callback_query(F.data == "p:close")
async def close_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _safe_edit(callback.message, "❌ Панель закрыта.")
    await callback.answer()


@router.callback_query(F.data == "p:auto")
async def auto(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _render_auto(callback)


@router.callback_query(F.data == "p:auto:on")
@router.callback_query(F.data == "p:auto:off")
async def auto_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _set_auto(callback.data.endswith(":on"))
    await _render_auto(callback)


@router.callback_query(F.data == "p:stats")
async def panel_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _render_stats(callback)


@router.callback_query(F.data == "p:settings")
async def panel_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _render_settings(callback)


@router.callback_query(F.data.startswith("p:threshold:"))
async def threshold(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    direction = 1 if callback.data.endswith(":up") else -1
    async with get_session_factory()() as session:
        config = await get_db_settings(session)
        config.auto_reply_threshold = max(
            0.50,
            min(1.00, round(config.auto_reply_threshold + direction * 0.05, 2)),
        )
        await session.commit()
    await _render_settings(callback)


@router.callback_query(F.data == "p:kb")
async def panel_knowledge(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _render_knowledge(callback)


@router.callback_query(F.data.startswith("p:kb:list:"))
async def faq_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        return await callback.answer("Кнопка устарела.", show_alert=True)
    await _render_faq_list(callback, page)


@router.callback_query(F.data == "p:kb:add")
async def faq_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await state.clear()
    await state.update_data(
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )
    await state.set_state(FAQDraft.question)
    await callback.message.answer(
        "➕ Введите вопрос для FAQ.\n"
        "Кнопка «Отмена» завершит добавление без записи в базу.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(FAQDraft.question)
async def faq_question(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    if not value:
        return await message.answer("Вопрос не может быть пустым.", reply_markup=cancel_keyboard())
    await state.update_data(question=value)
    await state.set_state(FAQDraft.keywords)
    await message.answer(
        "🔑 Введите ключевые слова через запятую или отправьте /skip.",
        reply_markup=cancel_keyboard(),
    )


@router.message(FAQDraft.keywords)
async def faq_keywords(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    await state.update_data(keywords="" if value.casefold() in {"/skip", "-"} else value)
    await state.set_state(FAQDraft.answer)
    await message.answer("✍️ Введите основной текст ответа.", reply_markup=cancel_keyboard())


@router.message(FAQDraft.answer)
async def faq_answer(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    if not value or value.casefold() in {"/skip", "-"}:
        return await message.answer("Текст ответа обязателен.", reply_markup=cancel_keyboard())
    await state.update_data(answer=value)
    await state.set_state(FAQDraft.variants)
    await message.answer(
        "🔁 Введите дополнительные варианты через | или отправьте /skip.",
        reply_markup=cancel_keyboard(),
    )


@router.message(FAQDraft.variants)
async def faq_variants(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    variants = [] if value.casefold() in {"/skip", "-"} else [
        item.strip() for item in value.replace("\n", "|").split("|") if item.strip()
    ]
    await state.update_data(variants=variants)
    data = await state.get_data()
    await state.set_state(FAQDraft.preview)
    preview = (
        "🔎 <b>Предпросмотр FAQ</b>\n\n"
        f"<b>Вопрос:</b> {escape(data['question'])}\n"
        f"<b>Ключевые слова:</b> {escape(data.get('keywords') or '—')}\n"
        f"<b>Ответ:</b> {escape(data['answer'])}\n"
        f"<b>Дополнительных вариантов:</b> {len(variants)}"
    )
    await message.answer(preview, reply_markup=faq_preview_keyboard())


@router.callback_query(F.data == "p:kb:save")
async def faq_save(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    data = await state.get_data()
    if not data.get("question") or not data.get("answer"):
        await state.clear()
        return await callback.answer("Черновик истёк. Начните добавление заново.", show_alert=True)
    async with get_session_factory()() as session:
        item = await add_faq_answer(
            session,
            data["question"],
            data.get("keywords"),
            data["answer"],
            variants=data.get("variants") or [],
        )
        await session.commit()
    await state.clear()
    await _safe_edit(
        callback.message,
        f"✅ FAQ #{item.id} сохранён.",
        knowledge_keyboard(),
    )
    await callback.answer("Сохранено")


@router.callback_query(F.data == "p:kb:search")
async def faq_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await state.clear()
    await state.update_data(
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )
    await state.set_state(FAQSearch.query)
    await callback.message.answer(
        "🔍 Введите текст для поиска по FAQ.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(FAQSearch.query)
async def faq_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    query = (message.text or "").strip()
    if not query:
        return await message.answer("Введите текст для поиска.", reply_markup=cancel_keyboard())
    data = await state.get_data()
    await state.clear()
    async with get_session_factory()() as session:
        matches = await search_faq_answers(session, query, limit=5)
    if not matches:
        text = "🔍 <b>Результаты поиска</b>\n\nСовпадений не найдено."
    else:
        text = "🔍 <b>Результаты поиска</b>\n\n" + "\n\n".join(
            f"<b>#{match.entry.id}</b> {escape((match.entry.question or match.entry.text)[:250])}\n"
            f"Уверенность: {match.confidence:.0%}\n{escape(match.entry.text[:350])}"
            for match in matches
        )
    await _edit_saved_panel(message, data, text, knowledge_keyboard())


@router.callback_query(F.data == "p:uq")
async def unanswered(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _render_unanswered_list(callback, 0)


@router.callback_query(F.data.startswith("p:uq:list:"))
async def unanswered_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        return await callback.answer("Кнопка устарела.", show_alert=True)
    await _render_unanswered_list(callback, page)


@router.callback_query(F.data.startswith("p:uq:item:"))
async def unanswered_item(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    try:
        question_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        return await callback.answer("Кнопка устарела.", show_alert=True)
    await _render_unanswered_detail(callback, question_id)


async def _start_unanswered_draft(callback: CallbackQuery, state: FSMContext, question_id: int):
    async with get_session_factory()() as session:
        item = await session.get(UnansweredQuestion, question_id)
    if not item:
        return await callback.answer("Вопрос не найден.", show_alert=True)
    await state.clear()
    await state.update_data(
        question_id=question_id,
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )
    await state.set_state(UnansweredDraft.answer)
    await callback.message.answer(
        "✍️ Введите ответ. Он будет добавлен в FAQ, после чего вопрос будет закрыт.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("p:uq:answer:"))
async def unanswered_answer_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _start_unanswered_draft(callback, state, int(callback.data.rsplit(":", 1)[1]))


@router.callback_query(F.data.startswith("p:uq:kb:"))
async def unanswered_kb_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _start_unanswered_draft(callback, state, int(callback.data.rsplit(":", 1)[1]))


@router.message(UnansweredDraft.answer)
async def unanswered_answer(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    answer = (message.text or "").strip()
    if not answer:
        return await message.answer("Ответ не может быть пустым.", reply_markup=cancel_keyboard())
    data = await state.get_data()
    async with get_session_factory()() as session:
        item = await session.get(UnansweredQuestion, data.get("question_id"))
        if not item:
            await state.clear()
            return await message.answer("Вопрос уже закрыт или не найден.")
        await add_faq_answer(
            session,
            item.question_text,
            "",
            answer,
            chat_id=item.chat_id,
            category="GENERAL",
        )
        await resolve_unanswered_question(session, item.id)
        await session.commit()
    await state.clear()
    await _edit_saved_panel(
        message,
        data,
        "✅ Ответ добавлен в FAQ, вопрос закрыт.",
        knowledge_keyboard(),
    )


@router.callback_query(F.data.startswith("p:uq:close:"))
async def unanswered_close(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    question_id = int(callback.data.rsplit(":", 1)[1])
    async with get_session_factory()() as session:
        item = await resolve_unanswered_question(session, question_id)
        await session.commit()
    if not item:
        return await callback.answer("Вопрос не найден.", show_alert=True)
    await _render_unanswered_list(callback, 0)


@router.callback_query(F.data.startswith("p:uq:ai:"))
async def unanswered_generate(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    question_id = int(callback.data.rsplit(":", 1)[1])
    async with get_session_factory()() as session:
        item = await session.get(UnansweredQuestion, question_id)
    if not item:
        return await callback.answer("Вопрос не найден.", show_alert=True)
    settings = get_settings()
    ai = OpenAIService(settings.ai_api_key, settings.openai_model, settings.ai_base_url)
    try:
        generated = await ai.analyze_with_reply(
            item.question_text,
            [],
            item.post_context or "",
        )
        answer = generated.reply.strip()
        category = generated.category
        if not answer:
            responses = await ai.responses(
                item.question_text,
                category,
                [],
                item.post_context or "",
            )
            answer = responses[0].text if responses else ""
        if not answer:
            raise RuntimeError("AI returned an empty answer")
    except Exception:
        return await callback.answer("Не удалось сгенерировать кандидата.", show_alert=True)
    async with get_session_factory()() as session:
        candidate = await create_answer_candidate(
            session,
            item.chat_id,
            item.comment_id,
            item.question_text,
            answer,
            category=category,
            post_context=item.post_context,
        )
        await session.commit()
    await callback.answer(f"Кандидат #{candidate.id} сохранён на проверку.")
    await _render_unanswered_detail(callback, question_id, answer=False)


@router.callback_query(F.data == "p:candidates")
async def candidates(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await _render_candidates(callback, 0)


@router.callback_query(F.data.startswith("p:candidates:"))
async def candidates_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        return await callback.answer("Кнопка устарела.", show_alert=True)
    await _render_candidates(callback, page)


@router.callback_query(F.data.startswith("p:ca:"))
async def candidate_approve(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    candidate_id = int(callback.data.rsplit(":", 1)[1])
    async with get_session_factory()() as session:
        item = await approve_answer_candidate(session, candidate_id)
        await session.commit()
    if not item:
        return await callback.answer("Кандидат не найден.", show_alert=True)
    await callback.answer("Кандидат одобрен и добавлен в FAQ.")
    await _render_candidates(callback, 0, answer=False)


@router.callback_query(F.data.startswith("p:cr:"))
async def candidate_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    candidate_id = int(callback.data.rsplit(":", 1)[1])
    async with get_session_factory()() as session:
        item = await reject_answer_candidate(session, candidate_id)
        await session.commit()
    if not item:
        return await callback.answer("Кандидат не найден.", show_alert=True)
    await callback.answer("Кандидат отклонён.")
    await _render_candidates(callback, 0, answer=False)


@router.callback_query(F.data == "p:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await state.clear()
    await _safe_edit(callback.message, "❌ Операция отменена.", panel_keyboard())
    await callback.answer()


# Compatibility with buttons generated by the previous panel version.
@router.callback_query(F.data == "panel:home")
@router.callback_query(F.data == "panel:stats")
@router.callback_query(F.data == "panel:ai")
@router.callback_query(F.data == "panel:settings")
@router.callback_query(F.data == "panel:knowledge")
async def legacy_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    if callback.data == "panel:home":
        await render_home(callback, edit=True)
    elif callback.data == "panel:stats":
        await _render_stats(callback, answer=False)
        return
    elif callback.data in {"panel:ai"}:
        await _render_auto(callback, answer=False)
    elif callback.data == "panel:settings":
        await _render_settings(callback, answer=False)
    else:
        await _render_knowledge(callback, answer=False)
        return
    await callback.answer()


@router.callback_query()
async def invalid_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Нет доступа", show_alert=True)
    await callback.answer("Кнопка устарела. Откройте /panel заново.", show_alert=True)