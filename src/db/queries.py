"""SQL-запросы. Все функции для БД"""

import logging
from ..db.db import get_db
from ..db.init_db import User, Subject, Queue
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from datetime import datetime, timedelta

# Импортируем логгер
logger = logging.getLogger(__name__)


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


def is_queue_in_db(subject_id: int, chat_id: int, lesson_date: int) -> bool:
    """Функция для проверки попытки создать существующую запись в QUEUE."""
    try:
        with get_db() as db:
            existing_queue = db.query(Queue).filter(
                Queue.subject_id == subject_id,
                Queue.chat_id == chat_id,
                Queue.lesson_date == lesson_date
            ).first()

            return existing_queue is not None
    except Exception as error:
        logger.error("Ошибка при проверки существования QUEUE")
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
        subject_id: int, chat_id: int, message_id: int,
        lesson_date: datetime, close_at: datetime,
        status: str,
        usernames: list):
    """Функция для добавления нового предмета в БД."""
    try:
        with get_db() as db:

            new_queue = Queue(
                subject_id=subject_id,
                chat_id=chat_id,
                message_id=message_id,
                lesson_date=lesson_date,
                close_at=close_at,
                status=status,
                usernames=usernames
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


def add_user_to_db(
        tg_username: str,
        chat_id: int, user_id: int,
        real_name: str = "",
        is_admin: bool = False):
    """Функция для добавления пользователя в БД."""
    with get_db() as db:
        try:
            new_user = User(
                tg_username=tg_username,
                chat_id=chat_id,
                user_id=user_id,
                real_name=real_name,
                is_admin=is_admin,
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"Добавлен новый user: {new_user.id}")
        except Exception as error:
            db.rollback()
            logger.error(f"Ошибка при добавлении пользователя в БД: {error}")


def get_user(chat_id: int, user_id: int) -> dict:
    """Функция для получения данных user"""
    try:
        with get_db() as db:

            user = db.query(User).filter(
                User.chat_id == chat_id,
                User.user_id == user_id
            ).first()

            if user:
                return user.__dict__
            else:
                raise ValueError(f"User с id '{user_id}'"
                                 f"и chat_id '{chat_id}' не найден.")
    except Exception as error:
        logger.error(f"Ошибка при получении данных пользователя {error}")
        raise ValueError(f"Ошибка при получении данных пользователя {error}")


def get_subject_id(chat_id: int, subject_name: str) -> int:
    """Функция для получения subject_id"""
    try:
        with get_db() as db:

            subject = db.query(Subject).filter(
                Subject.chat_id == chat_id,
                Subject.subject_name == subject_name
            ).first()

            if subject:
                return subject.id
            else:
                raise ValueError(f"Subject с name '{subject_name}'"
                                 f"и chat_id '{chat_id}' не найден.")
    except Exception as error:
        logger.error(f"Ошибка при получении subject id {error}")
        raise ValueError(f"Ошибка при получении subject id {error}")


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


def change_realname(tg_username: str, new_realname: str):
    """Функция для изменения realname пользователя."""
    try:
        with get_db() as db:
            tg_username = tg_username[1:]  # Убираем символ @
            realname_to_update = db.query(User).filter(
                func.lower(User.tg_username) == func.lower(tg_username)
            ).first()
            if realname_to_update:
                realname_to_update.real_name = new_realname
                db.commit()
                logger.info(
                    f"realname пользователя {tg_username} был изменён на "
                    f"{realname_to_update.real_name}")
            else:
                logger.warning(f"Пользователь {tg_username} не найден")
                raise ValueError(f"Пользователь {tg_username} не найден")
    except Exception as error:
        logger.error(f"Ошибка при изменении realname: {error}")
        raise ValueError(
            f"Ошибка при изменении realname: {error}")
