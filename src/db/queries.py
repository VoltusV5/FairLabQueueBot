"""SQL-запросы. Все функции для БД"""

import logging
from ..db.db import get_db
from ..db.init_db import User, Subject, Queue
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta

# Импортируем логгер
logger = logging.getLogger(__name__)


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


'''
def get_subject_id():
  """Функция для получения subject_id"""
   try:
        with get_db() as db:

            bd_request = db.query(Subject).filter(
                column == user_data
            ).first()
            return bd_request is not None
    except Exception as error:
        logger.error(f"Ошибка при проверки существования"
                     f"{user_data} в таблице {bd_model}")
        raise ValueError(
            f"Ошибка при проверки существования в БД: {error}")
'''


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
                is_admin=is_admin,
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"Добавлен новый user, userid: {new_user.id}")
        except Exception as error:
            db.rollback()
            logger.error(f"Ошибка при добавлении пользователя в БД: {error}")
