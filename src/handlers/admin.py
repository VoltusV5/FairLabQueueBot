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
from ..db.init_db import User, Subject, Queue
from sqlalchemy.exc import IntegrityError
from datetime import datetime

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


def split_queue_command_message(user_command):
    """Функция для разделения команды queue."""
    split_command = user_command.split()
    if len(split_command) >= 4:
        subject_time = split_command[-1]
        subject_date = split_command[-2]
        subject_name = "\"" + ' '.join(split_command[1:-2]) + "\""
        return (subject_name, subject_date, subject_time)
    else:
        raise ValueError(
            "Введены неправильные параметры команды queue при выполнении "
            "функции split_queue_command_message")


def is_in_db(user_data: str, bd_model: type, column_name: str) -> bool:
    """Функция для проверки существования записи в выбранной db.
    Вводится название пользователя/предмета и таблица, в которой это ищем
    """
    try:
        with get_db() as db:

            column = getattr(bd_model, column_name)
            bd_request = db.query(bd_model).filter(
                column == user_data
            ).first()
            return bd_request is not None
    except Exception as error:
        logger.error(f"Ошибка при проверки существования"
                     f"{user_data} в таблице {bd_model}")
        raise ValueError(
            f"Ошибка при проверки существования в БД: {error}")


def add_new_subject(chat_id: int, subject_name: str):
    """Функция для добавления нового предмета в БД."""
    try:
        with get_db() as db:
            new_subject = Subject(
                chat_id=chat_id,
                subject_name=subject_name,
            )

            db.add(new_subject)
            db.commit()
            db.refresh(new_subject)

            logger.info(
                f"Добавлен новый subject, subject_id: {new_subject.id}")
    except Exception as error:
        db.rollback()
        logger.error(f"Ошибка при добавлении предмета в БД: {error}")
        raise ValueError(
            f"Ошибка при добавлении предмета в БД: {error}")


def add_new_queue(
        subject_id: int, chat_id: int,
        lesson_date: datetime, close_date: datetime,
        status: str):
    """Функция для добавления нового предмета в БД."""
    try:
        with get_db() as db:

            new_queue = Queue(
                subject_id=subject_id,
                chat_id=chat_id,
                lesson_date=lesson_date,
                close_date=close_date,
                status=status
            )

            db.add(new_queue)
            db.commit()
            db.refresh(new_queue)

            logger.info(
                f"Добавлен новый queue, queue_id: {new_queue.id}")
    except IntegrityError as error:
        logger.error(f"Ошибка при добавлении записи в очередь в БД: {error}")
        raise ValueError(
            f"Ошибка при добавлении записи в очередь в БД: {error}")
    except Exception as error:
        db.rollback()
        logger.error(f"Ошибка при добавлении очереди в БД: {error}")
        raise ValueError(
            f"Ошибка при добавлении очереди в БД: {error}")


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
        print(lesson_date)
        # add_new_queue(chat_id, subject_date, subject_time)
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


def add_user_to_db(
        tg_username: str,
        chat_id: int,
        real_name: str = "",
        is_admin: bool = False):
    """Функция для добавления пользователя в БД."""
    with get_db() as db:
        try:
            new_user = User(
                tg_username=tg_username,
                chat_id=chat_id,
                real_name=real_name,
                is_admin=is_admin
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"Добавлен новый user, userid: {new_user.id}")
        except Exception as error:
            db.rollback()
            logger.error(f"Ошибка при добавлении пользователя в БД: {error}")


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
