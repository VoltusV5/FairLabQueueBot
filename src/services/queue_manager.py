"""
Бизнес-логика очередей:
добавление/удаление участников, сортировка, рандомизация,
формирование финальной очереди.
"""

# from aiogram import Bot, Dispatcher
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from ..db.init_db import Queue, Subject, SubmissionAttempt
from ..db.db import get_db2
from ..db.queries import (get_realname)
import logging
from random import randint


def get_queue(ms: Message, command: CommandObject | str):
    """Формирование финальной очереди"""
    db = get_db2()

    # получение пользователей из списка
    peoples = db.query(Queue).filter(
        Queue.message_id == ms.message_id).first().usernames
    peoples = ["@" + people for people in peoples]
    print("peoples", peoples)
    queue = []

    # получение учебного предмета
    subject = (
        db.query(Subject)
        .filter(Subject.subject_name == command,  # Чё то придумать с command, при нажатии на кнопку проблемно достать предмет, туда бы message_id
                Subject.chat_id == ms.chat.id)
        .first()
    )
    if not subject:
        """Найден ли предмет"""
        return -1

    # Заполняем queue кортежами (человек, попытки сдачи,
    # сколько раз не успел, среднее место, рандомное число).
    for people in peoples:
        temp = db.query(SubmissionAttempt)\
            .filter(SubmissionAttempt.tg_username == people,
                    SubmissionAttempt.subject_id == subject.id)\
            .first()
        if temp is None:
            temp = SubmissionAttempt(
                tg_username=people,
                subject_id=subject.id,
                history_position=[],
                missed_attempts_count=0
            )
            db.add(temp)
            db.commit()
        history = temp.history_position
        missed = temp.missed_attempts_count
        avg = sum([int(pos) for pos in history]) / len(history) if history else 0.0
        queue.append((people, history, missed, avg, randint(0, 100000)))

    queue.sort(key=lambda x: (len(x[1]), -x[2], -x[3], x[4]))
    print("queue", queue)

    # Добавляем текущую позицию в БД
    for idx, user in enumerate(queue):
        username, user_positions, cnt_missed, average_pos, random_digit = user
        temp = db.query(SubmissionAttempt)\
            .filter(SubmissionAttempt.tg_username == username,
                    SubmissionAttempt.subject_id == subject.id)\
            .first()
        temp.history_position = temp.history_position + [str(idx + 1)]
    db.commit()

    # Составляем строку для ТГ сообщения с очередью
    spisok = ''
    for idx, queue_element in enumerate(queue):

        queue_element_tgname = queue_element[0]
        queue_element_realname = get_realname(queue_element_tgname)
        spisok += (f'{idx + 1}. '
                   f'{queue_element_realname} '
                   f'({queue_element_tgname})\n')

    return spisok
