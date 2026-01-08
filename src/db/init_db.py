"""Создание/инициализация БД."""
from sqlalchemy import create_engine, Integer, String, Column, ForeignKey, DateTime,JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, Session
from datetime import datetime

SQL_URL = "sqlite:///./app.db"

engine = create_engine(
            SQL_URL,
            connect_args={"check_same_thread": False}
                       )

class Base(DeclarativeBase): pass

class Subject(Base):
    """Таблица названий списков"""
    __tablename__= "subject"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    name: Mapped[str] = Column(String)
    
class Queue(Base):
    """Очередь"""
    __tablename__="queue"

    id: Mapped[int] = Column(Integer, primary_key=True)

    subject_id: Mapped[int] = Column(ForeignKey("subject.id"))

    chat_id: Mapped[int] = Column(Integer)
    data: Mapped[datetime] = Column(DateTime)
    close_at: Mapped[datetime] = Column(DateTime)
    status:Mapped[str] = Column(String)



class User(Base):
    """Таблица пользователей"""
    __tablename__="users"

    id: Mapped[int] = Column(Integer, primary_key=True)
    name_tg: Mapped[str] = Column(String, unique=True)

class UserGeneral(Base):
    """Таблица люде, которые запускают голосование"""
    __tablename__ = "usergeneral"

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[str] = Column(ForeignKey("users.id"))

class QueueEntry(Base):
    """Таблица записи в очередь"""
    __tablename__="queue_entry"

    id: Mapped[int] = Column(Integer, primary_key=True)
    queue_id: Mapped[int] = Column(ForeignKey("queue.id"))
    user_id:Mapped[int] = Column(ForeignKey("users.id"))
    position: Mapped[int] = Column(Integer)


class SubmissionAttempt(Base):
    """Таблица для отслеживания на каком месте был пользователь"""

    __tablename__="queue_history"

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int] = Column(ForeignKey("users.id"))
    subject_id: Mapped[int] = Column(ForeignKey("subject.id"))
    history_position: Mapped[dict] = Column(JSON)


class Pay(Base):
    """Таблица для отслеживания подписки"""
    __tablename__="pay"

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id:Mapped[int] = Column(ForeignKey("usergeneral.id"))
    status: Mapped[bool] = Column(Boolean)
    date_pay: Mapped[datetime] = Column(DateTime)
    date_end: Mapped[datetime] = Column(DateTime)

Base.metadata.create_all(engine)
