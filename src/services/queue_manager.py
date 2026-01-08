"""
Бизнес-логика очередей:
добавление/удаление участников, сортировка, рандомизация,
формирование финальной очереди.
"""
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import message
from db.init_db import *


dp = Dispatcher()
Base.metadata.create_all(bind=engine)
def get_db():
    db:Session = SessionLocal()
    return db


@dp.message(Command(commands=["end"]))
async def get_queue(ms: message, command: CommandObject):
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
    
    
        
        
