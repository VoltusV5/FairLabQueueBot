"""Создание/инициализация БД."""

from sqlalchemy import Integer, String
from sqlalchemy import ForeignKey, DateTime, JSON, Boolean, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime


class Base(DeclarativeBase):
    """Родительский класс для таблиц БД"""


class Subject(Base):
    """Таблица названий предметов (например, ООП)"""

    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, index=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_name: Mapped[str] = mapped_column(
        String, nullable=False, unique=True)


class Queue(Base):
    """Логика для очереди"""

    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)

    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    chat_id: Mapped[int] = mapped_column(ForeignKey("subject.chat_id"))

    message_id: Mapped[int] = mapped_column(Integer)

    lesson_date: Mapped[datetime] = mapped_column(DateTime)
    close_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String)
    usernames: Mapped[list] = mapped_column(JSON)

    """Для того, чтобы исключить лишние проверки добавляем правило, что
    Если subject_id, lesson_date, chat_id совпадают, то выдаём исключение
    Зачем?
    """
    __table_args__ = (
        UniqueConstraint("subject_id", "chat_id", "lesson_date",
                         name="unique_subject_chat_lesson"),
    )


class User(Base):
    """Таблица пользователей"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    tg_username: Mapped[str] = mapped_column(
        String, unique=True, nullable=False)
    real_name: Mapped[str] = mapped_column(String, unique=True, nullable=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)


class SubmissionAttempt(Base):
    """Таблица для отслеживания истории на каком месте был пользователь"""

    __tablename__ = "queue_history"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    tg_username: Mapped[str] = mapped_column(ForeignKey("users.tg_username"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    history_position: Mapped[list] = mapped_column(JSON)


class Pay(Base):
    """Таблица для отслеживания подписки"""

    __tablename__ = "pay"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[bool] = mapped_column(Boolean)
    date_pay: Mapped[datetime] = mapped_column(DateTime)
    date_end: Mapped[datetime] = mapped_column(DateTime)
