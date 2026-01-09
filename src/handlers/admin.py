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
from ..db.queries import *
from ..db.init_db import User, Subject, Queue
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from enum import Enum

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


# Статусы для состояний Queue
class QueueStatus(Enum):
    """Статусы для состояний Queue"""

    WAITING_FOR_PARTICIPANTS = "waiting_for_participants"
    WAITING_FOR_LAST_PARTICIPANT = "waiting_for_last_participant"
    COMPLETED = "completed"


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
    try:
        if message.text is None:
            raise ValueError("Отправлено пустое сообщение")

        # Разделение сообщения на предмет, дату, время
        subject_name, subject_date, subject_time = split_queue_command_message(
            message.text)
        chat_id = message.chat.id

        # Кидаем сообщение
        await message.answer(
            text=f"📘 Запись на {subject_name}\n"
                 f"📅 {subject_date}\n"
                 f"⏰ {subject_time}",
            reply_markup=keyboard)

        # Проверяем существует ли subject_name в БД
        if not is_in_db(subject_name, Subject, "subject_name"):
            # Заполняем БД subject
            add_new_subject(chat_id, subject_name)

        subject_date_and_time = subject_date + ' ' + subject_time
        lesson_date = datetime.strptime(
            subject_date_and_time, "%d.%m.%Y %H:%M")
        close_queue_at = lesson_date - timedelta(hours=1)
        print(close_queue_at)
        status = QueueStatus.WAITING_FOR_PARTICIPANTS
        # add_new_queue(chat_id, lesson_date, close_queue_at, status)
        # Создаём новую Queue в БД

    except ValueError as error:
        # Сообщение пользователю об ошибке
        await message.answer(
            text=LEXICON_RU["/queue_error_message"],
        )
        logger.error(f"ValueError queue {error}")

    except Exception as error:
        logger.error(f"Ошибка команды queue {error}")
        raise ValueError(f"Ошибка команды queue {error}")


# Убирает "часики", которые показывают, что кнопка не работает
@router.callback_query(F.data.in_(["confirm_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    """Записывает пользователя в очередь."""

    # Получаем имя пользователя и проверяем, существует ли он в БД
    try:
        tg_username = callback.from_user.username
        if callback.message is None:
            raise ValueError("Ошибка при проверке сообщения для голосования")
        chat_id = callback.message.chat.id
        if tg_username is None:
            raise ValueError("tg_username не может быть None")
        if not is_in_db(tg_username, User, "tg_username"):
            add_user_to_db(tg_username, chat_id)
        else:
            logger.info("Пользователь уже есть в БД")
    except Exception as error:
        logger.error(f"Ошибка при проверке пользователя {error}")
        raise ValueError(f"Ошибка при проверке пользователя {error}")


    await callback.answer(text="Вы записаны ✅")

    """Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    """
    """Переместить в файл student.py"""
