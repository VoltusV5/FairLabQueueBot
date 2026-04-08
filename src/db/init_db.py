"""Создание/инициализация БД."""

from sqlalchemy import (
    BigInteger,
    Integer,
    String,
    ForeignKey,
    DateTime,
    JSON,
    Boolean,
    UniqueConstraint,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime


class Base(DeclarativeBase):
    """Родительский класс для таблиц БД"""


class User(Base):
    """Пользователь Telegram: ключ — tg_id."""

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Chat(Base):
    """Чат и подписка (оплата за чат)."""

    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # trial | base | supervip | expired
    subscription_tier: Mapped[str] = mapped_column(String(32), default="trial")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    subscription_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Глобальный переключатель автозакрытия для новых очередей (/auto)
    autoclose_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Кастомные правила (/newautorule), иначе None — дефолт из compute_default_autoclose
    autoclose_rules: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Напоминания о подписке: {"deadline_iso": "...", "d3": bool, "d1": bool}
    subscription_reminder_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Группы чата: [[tg_id1, tg_id2, ...], ...]
    groups: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Subject(Base):
    """Название предмета в контексте чата."""

    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, index=True, autoincrement=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    subject_name: Mapped[str] = mapped_column(String(512), nullable=False)

    __table_args__ = (
        UniqueConstraint("chat_id", "subject_name", name="uq_chat_subject_name"),
    )


class Queue(Base):
    """Очередь на занятие."""

    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)

    lesson_date: Mapped[datetime] = mapped_column(DateTime)
    # Когда закрыть набор (None — без автозакрытия по таймеру)
    close_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    status: Mapped[str] = mapped_column(String(64))
    # Список tg_id участников (порядок записи или итоговый порядок)
    participants: Mapped[list] = mapped_column(JSON, default=list)
    # Доп. поля: refused, voting_started_at, auto_close, swap и т.д.
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subject_id", "chat_id", "lesson_date", name="unique_subject_chat_lesson"
        ),
    )


class SubmissionAttempt(Base):
    """История позиций и пропусков по предмету (предмет привязан к чату)."""

    __tablename__ = "queue_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    history_position: Mapped[list] = mapped_column(JSON, default=list)
    missed_attempts_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint("tg_id", "subject_id", name="uq_history_user_subject"),
    )


class PaymentRecord(Base):
    """Платёж YooKassa (привязка к чату)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    yookassa_payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"))
    tier: Mapped[str] = mapped_column(String(32))
    amount_rub: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PresencePoll(Base):
    """Кто сейчас на паре — голосование по сообщению."""

    __tablename__ = "presence_polls"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    here_tg_ids: Mapped[list] = mapped_column(JSON, default=list)


class SwapRequest(Base):
    """Заявка на обмен местами (по сообщению очереди)."""

    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    queue_message_id: Mapped[int] = mapped_column(Integer, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    from_tg_id: Mapped[int] = mapped_column(BigInteger)
    to_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    swap_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
