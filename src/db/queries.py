"""SQL-запросы. Все функции для БД"""

import logging
from ..db.db import get_db
from ..db.init_db import User, Subject, Queue, SubmissionAttempt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func, delete
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

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


def add_tgname_in_queue(tg_username: str, message_id: int):
    """Функция для добавления пользователя в список"""
    with get_db() as db:
        try:
            queue = db.query(Queue).filter(
                Queue.message_id == message_id).first()
            if queue.usernames is None:
                queue.usernames = []

            if tg_username in queue.usernames:
                return -1
            else:
                queue.usernames.append(tg_username)

            flag_modified(queue, "usernames")
            db.commit()
            logger.info(f"Добавлен новый "
                        f"tg_username в usernames: {tg_username}")
        except Exception as error:
            db.rollback()
            logger.error(
                "Ошибка при добавлении tg_username в список usernames")
            raise ValueError(f"Ошибка при добавлении "
                             f"tg_username в список usernames {error}")


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


def remove_tgname_in_queue(
        tg_username: str, message_id: int, subject_id: int, chat_id: int):
    """Функция для удаления пользователя из записи на предмет
    Обновляет Queue
    """
    with get_db() as db:
        # убираем из списка очереди
        try:
            queue: Queue = db.query(Queue).filter(
                Queue.chat_id == chat_id,
                Queue.message_id == message_id,
                Queue.subject_id == subject_id).first()

            if tg_username not in queue.usernames:
                return

            index = queue.usernames.index(tg_username)
            del queue.usernames[index]

            flag_modified(queue, "usernames")
            db.commit()
            logger.info(f"Удален из списка очереди "
                        f"tg_username в usernames: {tg_username}")
            return -1
        except Exception as e:
            db.rollback()
            logger.error("Ошибка при удалении из Queue")
            print(e)


def add_submission_attempt(tgname: str, subject_id: int):
    """Если у пользователя не было записи в Submission Attempt
    Создаёт ему такую запись
    """
    with get_db() as db:
        try:
            if db.query(SubmissionAttempt).filter(
                    SubmissionAttempt.subject_id == subject_id,
                    SubmissionAttempt.tg_username == tgname).first() is None:
                new_attempt = SubmissionAttempt(
                    tg_username=tgname, subject_id=subject_id,
                    history_position=[])
                db.add(new_attempt)
                db.commit()
                db.refresh(new_attempt)
        except Exception as e:
            db.rollback()
            logger.error("Ошибка при добавлении в SubmisiionAttempt")
            print(e)


def add_history_position(queue: list[tuple], subject_id: int):
    """
    Функция для добавления позиции пользователя в историю позиций
    chat_id и subject_name для того чтобы достать subject_id
    """
    # Запись выглядит так чуть поменять кое что
    # [["1", "tgname1"], ["2", "tgname2"]] , на айди потом изи свапнуть
    with get_db() as db:
        try:
            for pos, tgname in queue:
                tgname = "@" + tgname.replace(" ", "")

                add_submission_attempt(tgname, subject_id)

                people_history: SubmissionAttempt = (
                    db.query(SubmissionAttempt)
                    .filter(SubmissionAttempt.subject_id == subject_id,
                            SubmissionAttempt.tg_username == tgname
                            ).first())
                if people_history.history_position in [None, []]:
                    people_history.history_position = [int(pos)]
                else:
                    people_history.history_position[-1] = (
                        int(people_history.history_position[-1]
                            .replace("*", "")))

                    people_history.history_position.append(int(pos))

                    flag_modified(people_history, "history_position")
                    db.commit()
                    logger.info(f"Обновлена история позиций у {tgname}")
        except Exception as e:
            # Если пользователь один в списке то лог выдаёт ошибку
            # т.к. при split в position будет [[1, "@tg"], ['']],
            # и пустая вызовёт ошибку но она ни на что не влияет

            db.rollback()
            logger.error("Ошибка при обновлении списка позиций")
            print(e)


def remove_queue(
        username: str, chat_id: int, message_id: int, subject_id: int):
    """Полное удаление Queue строки"""
    with get_db() as db:
        try:
            stm = delete(Queue).where(
                Queue.message_id == message_id,
                Queue.chat_id == chat_id,
                Queue.subject_id == subject_id)
            db.execute(stm)
            db.commit()
            logger.info("Успешное удаление Queue в {chat_id}")
        except Exception as e:
            print(e)
            logger.error("Ошибка при удалении всей очереди Queue в {chat_id}")


def save_position_not_pass(queue: list[tuple], subject_id):
    """Функция которая пометит позиции людей, которые не успели сдать"""
    with get_db() as db:
        try:
            for pos, tgname in queue:
                people_history: SubmissionAttempt = db.query(
                    SubmissionAttempt).filter(
                        SubmissionAttempt.subject_id == subject_id,
                        SubmissionAttempt.tg_username == tgname
                ).first()

                if people_history.history_position in [None, []]:
                    people_history.history_position = [str(pos) + "*"]
                else:
                    people_history.history_position[-1] = str(pos) + "*"
                    flag_modified(people_history, "history_position")

                    db.commit()
                    logger.info(
                        f"Закреплено место на следующий урок у {tgname}")

        except Exception as e:
            print(e)
            logger.error("Ошибка при отметке людей которые не успели сдать")
