"""
Обработчики команд админа:
создание и закрытие очередей, перемешивание, сброс статистики, экспорт данных.
"""

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)

from ..db.db import get_db
from ..db.init_db import User

import logging
from src.lexicon import LEXICON_RU

# Импортируем логгер
logger = logging.getLogger(__name__)

# Инициализируем роутер уровня модуля
router = Router()

# Создаем объект инлайн-кнопок
btn_participate = InlineKeyboardButton(
    text="✅ Участвую", callback_data="confirm_participation"
)

# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn_participate]])


# Этот хэндлер срабатывает на команду /start
@router.message(CommandStart())
async def process_start_command(message: Message):
    """Выводит сообщение, что бот запущен."""
    await message.answer(text=LEXICON_RU['/start'])


# Этот хэндлер срабатывает на команду /help
@router.message(Command(commands='help'))
async def process_help_command(message: Message):
    """Выводит информацию о командах бота, доступных админу."""
    await message.answer(text=LEXICON_RU['/help'])


# Этот хэндлер срабатывает на команду /queue
@router.message(Command(commands='queue'))
async def process_queue_command(message: Message):
    """Отправляет сообщение с записью в очередь."""
    await message.answer(
        text=LEXICON_RU['/queue'], reply_markup=keyboard
    )


def add_user_to_db(
        chat_id, ######
        tg_username: str,
        real_name: str = "",
        is_admin: bool = False):
    """Функция для добавления пользователя в БД."""
    with get_db() as db:
        try:
            new_user = User(
                tg_username=tg_username,
                real_name=real_name,
                is_admin=is_admin,
                chat_id=chat_id # добавление chat_id
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"Новый user добавлен, userid: {new_user.id}")
        except Exception as error:
            db.rollback()
            logger.error(f"Ошибка при добавлении пользователя в БД: {error}")


# Убирает "часики", которые показывают, что кнопка не работает
@router.callback_query(F.data.in_(["confirm_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    """Записывает пользователя в очередь."""
    user_tg_username = callback.from_user.username
    chat_id = callback.message.chat.id      # Добавление chat_id
    add_user_to_db(chat_id, user_tg_username)
    await callback.answer(text="Вы записаны ✅")

    """Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    """
    """Переместить в файл student.py"""
