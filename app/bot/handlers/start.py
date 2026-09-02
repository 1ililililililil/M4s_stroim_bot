from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.config import get_settings
from app.performance import timed_command

router = Router()

@router.message(Command("start"))
@timed_command("/start")
async def start(message: Message):
    if message.from_user.id not in get_settings().admins:
        await message.answer("⛔ У вас нет доступа к панели администратора.")
        return
    await message.answer("👨‍🚒 MCHS AI Admin Bot\n\nБот готов помогать с комментариями.\n\n/panel — панель\n/settings — настройки\n/stats — статистика")

@router.message(Command("myid"))
@timed_command("/myid")
async def myid(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")
