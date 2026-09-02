from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Автоответы", callback_data="p:auto"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="p:stats")],
        [InlineKeyboardButton(text="🧠 База знаний", callback_data="p:kb"),
         InlineKeyboardButton(text="❓ Неотвеченные", callback_data="p:uq")],
        [InlineKeyboardButton(text="💡 Кандидаты", callback_data="p:candidates"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data="p:settings")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="p:refresh"),
         InlineKeyboardButton(text="❌ Закрыть", callback_data="p:close")]
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="p:home")]
    ])


def auto_keyboard(enabled: bool):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔴 Выключить автоответы" if enabled else "🟢 Включить автоответы",
            callback_data="p:auto:off" if enabled else "p:auto:on"
        )],
        [InlineKeyboardButton(text="📊 Статистика автоответов", callback_data="p:stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:home")]
    ])


def settings_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Включить автоответы", callback_data="p:auto:on"),
         InlineKeyboardButton(text="🔴 Выключить автоответы", callback_data="p:auto:off")],
        [InlineKeyboardButton(text="🎯 Порог −5%", callback_data="p:threshold:down"),
         InlineKeyboardButton(text="🎯 Порог +5%", callback_data="p:threshold:up")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:home")]
    ])


def knowledge_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ответ", callback_data="p:kb:add"),
         InlineKeyboardButton(text="📚 Список FAQ", callback_data="p:kb:list:0")],
        [InlineKeyboardButton(text="🔍 Найти", callback_data="p:kb:search")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:home")]
    ])


def faq_list_keyboard(page: int, has_next: bool):
    controls = []
    if page > 0:
        controls.append(InlineKeyboardButton(text="◀️", callback_data=f"p:kb:list:{page - 1}"))
    if has_next:
        controls.append(InlineKeyboardButton(text="▶️", callback_data=f"p:kb:list:{page + 1}"))
    rows = [controls] if controls else []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="p:kb")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def unanswered_list_keyboard(page: int, has_next: bool):
    controls = []
    if page > 0:
        controls.append(InlineKeyboardButton(text="◀️", callback_data=f"p:uq:list:{page - 1}"))
    if has_next:
        controls.append(InlineKeyboardButton(text="▶️", callback_data=f"p:uq:list:{page + 1}"))
    rows = [controls] if controls else []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="p:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def unanswered_detail_keyboard(question_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Добавить свой ответ", callback_data=f"p:uq:answer:{question_id}")],
        [InlineKeyboardButton(text="🧠 Добавить в базу", callback_data=f"p:uq:kb:{question_id}")],
        [InlineKeyboardButton(text="🤖 Сгенерировать кандидата", callback_data=f"p:uq:ai:{question_id}")],
        [InlineKeyboardButton(text="🗑 Закрыть/решить", callback_data=f"p:uq:close:{question_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:uq:list:0")]
    ])


def candidate_list_keyboard(page: int, has_next: bool, candidate_ids=()):
    rows = [
        [InlineKeyboardButton(text=f"✅ Одобрить #{cid}", callback_data=f"p:ca:{cid}"),
         InlineKeyboardButton(text=f"❌ Отклонить #{cid}", callback_data=f"p:cr:{cid}")]
        for cid in candidate_ids
    ]
    controls = []
    if page > 0:
        controls.append(InlineKeyboardButton(text="◀️", callback_data=f"p:candidates:{page - 1}"))
    if has_next:
        controls.append(InlineKeyboardButton(text="▶️", callback_data=f"p:candidates:{page + 1}"))
    if controls:
        rows.append(controls)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="p:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faq_preview_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="p:kb:save"),
         InlineKeyboardButton(text="✏️ Отменить", callback_data="p:cancel")]
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="p:cancel")]
    ])