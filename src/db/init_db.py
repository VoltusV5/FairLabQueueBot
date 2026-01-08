"""Создание/инициализация БД."""

from sqlalchemy import create_engine, Integer, String, Column, ForeignKey, DateTime, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, sessionmaker, Session
from datetime import datetime


class Base(DeclarativeBase):
    pass


class Subject(Base):
    """Таблица названий списков"""
    __tablename__ = "subject"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = Column(Integer)
    name: Mapped[str] = Column(String)


class Queue(Base):
    """Очередь"""
    __tablename__ = "queue"

    id: Mapped[int] = Column(Integer, primary_key=True)

    subject_id: Mapped[int] = Column(ForeignKey("subject.id"))

    chat_id: Mapped[int] = Column(Integer)
    data: Mapped[datetime] = Column(DateTime)
    close_at: Mapped[datetime] = Column(DateTime)
    status: Mapped[str] = Column(String)


class User(Base):
    """Таблица пользователей"""
    __tablename__ = "users"

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[str] = Column(String, unique=True, nullable=False)
    real_name: Mapped[str] = Column(String, unique=True, nullable=True)
    is_admin: Mapped[bool] = Column(Boolean, nullable=False, default=False)
    chat_id: Mapped[int] = Column(Integer)


class SubmissionAttempt(Base):
    """Таблица для отслеживания на каком месте был пользователь"""

    __tablename__ = "queue_history"

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int] = Column(ForeignKey("users.id"))
    subject_id: Mapped[int] = Column(ForeignKey("subject.id"))
    history_position: Mapped[dict] = Column(JSON)


class Pay(Base):
    """Таблица для отслеживания подписки"""
    __tablename__ = "pay"

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int] = Column(ForeignKey("users.id"))
    status: Mapped[bool] = Column(Boolean)
    date_pay: Mapped[datetime] = Column(DateTime)
    date_end: Mapped[datetime] = Column(DateTime)
