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
from ..db.queries import (
    add_user_to_db, add_new_queue, add_new_subject, is_in_db,
    split_queue_command_message, get_subject_id, add_tgname_in_queue
)
from ..db.init_db import User, Subject, Queue
from src.services.queue_manager import get_queue
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
btn_participate = [
    InlineKeyboardButton(text="✅ Участвую", callback_data="confirm_participation"),
    InlineKeyboardButton(text="Завершить досрочно", callback_data="close_queue")
    ]


# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=[btn_participate])


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
    """Отправляет сообщение с записью в очередь. Создаёт таблицу в БД Subject
    Создаёт таблицу Queue.
    """
    print(message.message_id)
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

        # Переменные для создания БД Queue
        subject_date_and_time = subject_date + ' ' + subject_time
        subject_id = get_subject_id(chat_id, subject_name)
        chat_id = message.chat.id
        message_id = message.message_id + 1  ## Сделал костыль не уверен что всегда будет работать, тк разные айдишники у сообшений на 1 отличаются
        lesson_date = datetime.strptime(
            subject_date_and_time, "%d.%m.%Y %H:%M")
        close_at = lesson_date - timedelta(hours=1)
        status = QueueStatus.WAITING_FOR_PARTICIPANTS
        usernames: list = list()
        # Создаём новую Queue в БД
        add_new_queue(
            subject_id, chat_id, message_id, lesson_date,
            close_at, status.value, usernames)

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
    """Записывает пользователя в очередь при нажатии кнопки УЧАСТВУЮ."""
    # Получаем имя пользователя и проверяем, существует ли он в БД
    print(callback.message.message_id)
    try:
        tg_username = callback.from_user.username
        if callback.message is None:
            raise ValueError("Ошибка при проверке сообщения для голосования")
        chat_id = callback.message.chat.id
        if tg_username is None:
            raise ValueError("tg_username не может быть None")
        if not is_in_db(tg_username, User, "tg_username"):
            add_user_to_db(tg_username, chat_id)
        
        if not is_in_db(callback.message.message_id, Queue, "message_id"):
            raise ValueError("Предмет не найден, ошибка в добавлении очереди")
        if is_in_db(callback.message.message_id, Queue, "message_id"):
            if add_tgname_in_queue(callback.from_user.username, callback.message.message_id) == -1:
                await callback.answer("Вы уже записаны!")
                logger.info("Пользователь уже есть в БД")
            else:
                await callback.answer(text="Вы записаны ✅")
                logger.info("Пользователь успешно добавлен в список")

    
    except Exception as error:
        logger.error(f"Ошибка при проверке пользователя {error}")
        raise ValueError(f"Ошибка при проверке пользователя {error}")

    

    """Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    """
    """Переместить в файл student.py"""



@router.callback_query(F.data.in_(["close_queue"]))
async def process_buttons_click(callback: CallbackQuery):
    """Завершение досрочное только админам"""
    ## Но фильтр позже добавим чтобы тестить было проще
    await callback.message.edit_reply_markup(reply_markup=None)  # Убираем кнопки
    text = callback.message.text
    subject = text[text.find("на")+3:].split("\n")[0]   ##Забрал название предмета для кнопки
    head = f"Список на {subject}\n{ text[text.find("на")+3:].split("\n")[1]}\n{ text[text.find("на")+3:].split("\n")[2]}\n\n"
    queue : str = head + get_queue(callback.message, subject)
    print(queue)
    await callback.message.answer(queue)