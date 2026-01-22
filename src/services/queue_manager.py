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
import logging


def check_people(queue) -> str:
    """Функция которая обработает людей которые не успели сдать в прошлый раз 
    Она создаёт список из людей у которых есть пометки со *"""
    spisok = ""
    queue_new = []
    for indx, history in enumerate(queue):
        if isinstance(history[1][-1], str):
            pos = int(history[1][-1].replace("*", ""))
            queue_new.append((history[0], pos))
            del queue[indx]

    queue_new = sorted(queue_new, key=lambda x: x[1])
    if queue_new != []:
        for i in range(len(queue_new)):
            spisok += f"{i+1}. {queue_new[i][0]}\n"

    return spisok

def get_queue(ms: Message, command: CommandObject | str):
    """Формирование финальной очереди"""
    db = get_db2()
    # получение пользователей из списка
    peoples = db.query(Queue).filter(
        Queue.message_id == ms.message_id).first().usernames
    queue = []
    # получение учебного предмета
    subject = (
        db.query(Subject)
        .filter(Subject.subject_name == command,             ### Чё то придумать с command, при нажатии на кнопку проблемно достать предмет, туда бы message_id 
                Subject.chat_id == ms.chat.id)
        .first()
    )
    if not subject:
        """Найден ли предмет"""
        return -1 

    # Заполняем queue кортежами (человек, попытки сдачи).
    for people in peoples:
        temp = db.query(SubmissionAttempt)\
            .filter(SubmissionAttempt.tg_username == people,
                    SubmissionAttempt.subject_id == subject.id)\
            .first()

        history = temp.history_position if temp else [0]
        queue.append((people, history))

    spisok = check_people(queue)

    queue = sorted(
        queue, 
        key=lambda x: sum(x[1]) / len(x[1]),
        reverse=True
        )
    
    for i in range(len(queue)):
        spisok += f'{i+1}. @{queue[i][0]}\n'

    return spisok

