"""
Бизнес-логика очередей:
добавление/удаление участников, сортировка, рандомизация,
формирование финальной очереди.
"""

# from aiogram import Bot, Dispatcher
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from ..db.init_db import User, Subject, SubmissionAttempt
from ..db.db import get_db2
import logging


def get_queue(db, ms: Message, command: CommandObject):
    """Формирование финальной очереди"""
    db = get_db2()
    # получение всех пользователей текущего чата
    peoples = db.query(User).filter(User.chat_id == ms.chat.id).all()
    queue = []
    # получение учебного предмета
    subject = (
        db.query(Subject)
        .filter(Subject.subject_name == command.args,
                Subject.chat_id == ms.chat.id)
        .first()
    )
    if not subject:
        """Найден ли предмет"""
        return -1 

    # Заполняем queue кортежами (человек, попытки сдачи).
    for people in peoples:
        temp = db.query(SubmissionAttempt)\
            .filter(SubmissionAttempt.user_id == people.id,
                    SubmissionAttempt.subject_id == subject.id)\
            .first()

        history = temp.history_position if temp else [0]
        queue.append((people, history))

    queue = sorted(
        queue, 
        key=lambda x: sum(x[1]) / len(x[1]),
        reverse=True
        )
    spisok = ""
    for i in range(len(queue)):
        spisok += f'{i+1}. {queue[i][0].tg_username}\n'

    return spisok

