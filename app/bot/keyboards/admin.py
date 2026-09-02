from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def admin_keyboard(comment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Ответить", callback_data=f"reply:{comment_id}:1"),
         InlineKeyboardButton(text="2️⃣ Ответить", callback_data=f"reply:{comment_id}:2"),
         InlineKeyboardButton(text="3️⃣ Ответить", callback_data=f"reply:{comment_id}:3")],
        [InlineKeyboardButton(text="🔄 Ещё варианты", callback_data=f"regen:{comment_id}"),
         InlineKeyboardButton(text="✏️ Свой ответ", callback_data=f"custom:{comment_id}")],
        [InlineKeyboardButton(text="⏭ Игнорировать", callback_data=f"ignore:{comment_id}")],
    ])

def moderation_keyboard(comment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{comment_id}"),
         InlineKeyboardButton(text="⏭ Оставить", callback_data=f"ignore:{comment_id}")],
        [InlineKeyboardButton(text="🔄 Повторить AI-анализ", callback_data=f"reanalyze:{comment_id}")]
    ])
