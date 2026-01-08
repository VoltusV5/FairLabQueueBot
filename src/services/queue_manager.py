"""
Бизнес-логика очередей:
добавление/удаление участников, сортировка, рандомизация,
формирование финальной очереди.
"""
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from db.init_db import *
import random



@dp.message(Command(commands=["end"]))
async def get_queue(ms: Message, command: CommandObject):
    db = get_db()
    peoples = db.query(User).filter(User.chat_id==ms.chat.id).all()
    queue = []
    subject = (
        db.query(Subject)
        .filter(Subject.name == command.args)
        .first()
        )
    if not subject:
        await ms.answer("Предмет не найден")
        return
    
    for people in peoples:
        temp = db.query(SubmissionAttempt)\
            .filter(SubmissionAttempt.user_id==people.id, 
                    SubmissionAttempt.subject_id==subject.id)\
                        .first()
                    

        history = temp.history_position if temp else []
        queue.append((people, history))
    k = 0
    for i in queue:
        if i[1] != []:
            continue
        else:
            k+=1
    
    if k <=10:

        queue = sorted(queue, key=lambda x: sum(x[1])/len(x[1]), reverse=True)
        spisok = ""
        for i in range(len(queue)):
            spisok += f'{i+1}. {queue[i][1].name_tg}\n'
        k=0
        await ms.answer(spisok) 
        
    else:
        spisok = ""
        for i in range(len(queue)):
            spisok += f'{i+1}. {queue[i][1].name_tg}\n'
        k=0
        await ms.answer(spisok) 

