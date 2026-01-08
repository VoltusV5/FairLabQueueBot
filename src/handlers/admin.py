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
from sqlalchemy.orm import Session
from ..db.db import SessionLocal
# from ..db.db import get_db

from ..db.init_db import User
import logging
from src.lexicon import LEXICON_RU


logger = logging.getLogger(__name__)

# Инициализируем роутер уровня модуля
router = Router()

# Создаем объект инлайн-кнопок
btn_participate = InlineKeyboardButton(
    text="✅ Участвую", callback_data="user_id"
)

# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn_participate]])


# Этот хэндлер срабатывает на команду /start
@router.callback_query(CommandStart())
async def process_start_command(callback: CallbackQuery):
    '''Выводит сообщение, что бот запущен.'''

    await callback.answer(text=LEXICON_RU['/start'])


# Этот хэндлер срабатывает на команду /help
@router.callback_query(Command(commands='help'))
async def process_help_command(callback: CallbackQuery):
    '''Выводит информацию о командах бота, доступных админу.'''

    await callback.answer(text=LEXICON_RU['/help'], show_alert=True)


# Этот хэндлер срабатывает на команду /queue
@router.message(Command(commands='queue'))
async def process_queue_command(message: Message):
    '''Отправляет сообщение с записью в очередь'''

    await message.answer(
        text=LEXICON_RU['/queue'], reply_markup=keyboard
    )


def add_user_to_db(
        tg_id: int,
        real_name: str = "",
        is_admin: bool = False):
    '''Функция для добавления пользователя в БД'''
    db = SessionLocal()
    try:
        print(tg_id)
        new_user = User(
            tg_id=tg_id,
            real_name=real_name,
            is_admin=is_admin
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        logger.info(f"Новый предмет добавлен с ID: {new_user.id}")
    except:
        db.rollback()
        logger.error("Ошибка при добавлении пользователя")
    db.close()


# Убирает "часики", которые показывают, что кнопка не работает
@router.callback_query(F.data.in_(["confirm_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    '''Записывает пользователя в очередь'''

    user_tg_username = callback.from_user.username
    add_user_to_db(user_tg_username)
    await callback.answer(text="Вы записаны ✅")

    '''Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    '''
    '''Переместить в файл student.py'''
