"""Модуль описания ORM-моделей базы данных SQLAlchemy.

Определяет структуру всех таблиц системы: пользователи, чаты, предметы,
очереди, история сдач, платежи и голосования присутствия.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class QueueStatus(StrEnum):
    """Статусы жизненного цикла очереди."""

    WAITING_FOR_PARTICIPANTS = "waiting_for_participants"
    WAITING_FOR_LAST_PARTICIPANT = "waiting_for_last_participant"
    COMPLETED = "completed"


class SwapStatus(StrEnum):
    """Статусы заявок на обмен местом."""

    OPEN = "open"
    AWAIT_ACCEPT = "await_accept"
    DONE = "done"


class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy."""


class User(Base):
    """Модель пользователя Telegram.

    Attributes:
        tg_id: Уникальный Telegram ID пользователя (первичный ключ).
        tg_username: Юзернейм пользователя в Telegram без символа @.
        real_name: Реальное ФИО или имя пользователя для отображения.
        updated_at: Дата и время последнего обновления информации.
    """

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def display_name(self) -> str:
        """Возвращает форматированное имя пользователя для отображения."""
        if self.real_name:
            username_part = f"@{self.tg_username}" if self.tg_username else ""
            return f"{self.real_name} {username_part}".strip()
        if self.tg_username:
            return f"@{self.tg_username}"
        return str(self.tg_id)


class Chat(Base):
    """Модель чата и настроек подписки.

    Attributes:
        chat_id: Уникальный ID группы/чата Telegram.
        title: Название группы.
        subscription_tier: Уровень подписки (trial, base, supervip, expired).
        trial_ends_at: Дата окончания пробного периода.
        subscription_ends_at: Дата окончания платной подписки.
        created_at: Дата добавления чата в систему.
        autoclose_enabled: Включено ли автоматическое закрытие очередей.
        autoclose_rules: Кастомные правила автозакрытия очередей.
        subscription_reminder_state: Состояние отправленных уведомлений.
        groups: Списки групп/бригад участников чата.
        admins: Список Telegram ID администраторов чата.
    """

    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subscription_tier: Mapped[str] = mapped_column(String(32), default="trial")
    pending_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pending_tier_activates_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    autoclose_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    autoclose_rules: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    subscription_reminder_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    groups: Mapped[list[list[int]] | None] = mapped_column(JSON, nullable=True)
    admins: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)


class Subject(Base):
    """Модель учебного предмета в чате.

    Attributes:
        id: Идентификатор предмета.
        chat_id: ID чата, к которому привязан предмет.
        subject_name: Название предмета.
        kings: Список Telegram ID "королей бригад".
    """

    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, index=True, autoincrement=True
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    subject_name: Mapped[str] = mapped_column(String(512), nullable=False)
    kings: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "chat_id", "subject_name", name="uq_chat_subject_name"
        ),
    )


class Queue(Base):
    """Модель очереди на лабораторную работу/занятие.

    Attributes:
        id: Уникальный ID очереди.
        subject_id: Внешний ключ на учебный предмет.
        chat_id: ID чата Telegram.
        message_id: ID сообщения с интерактивной очередью.
        lesson_date: Дата проведения занятия.
        close_at: Дата/время автоматического закрытия записи.
        created_at: Время создания очереди.
        status: Статус очереди (waiting_for_participants, closed и т.д.).
        participants: Список записанных участников (Telegram ID или текстовые).
        extra: Дополнительные параметры (обмены, голосования).
    """

    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    chat_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)

    lesson_date: Mapped[datetime] = mapped_column(DateTime)
    close_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )

    status: Mapped[str] = mapped_column(String(64))
    participants: Mapped[list[int | str]] = mapped_column(JSON, default=list)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "chat_id",
            "lesson_date",
            name="unique_subject_chat_lesson",
        ),
    )


class SubmissionAttempt(Base):
    """Модель истории сдачи лабораторных работ и пропусков.

    Attributes:
        id: Идентификатор записи в истории.
        tg_id: Внешний ключ на пользователя Telegram.
        subject_id: Внешний ключ на учебный предмет.
        history_position: История ранее полученных позиций.
        missed_attempts_count: Количество пропущенных сдач.
    """

    __tablename__ = "queue_history"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    history_position: Mapped[list[Any]] = mapped_column(JSON, default=list)
    missed_attempts_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint(
            "tg_id", "subject_id", name="uq_history_user_subject"
        ),
    )


class PaymentRecord(Base):
    """Модель платежной транзакции YooKassa.

    Attributes:
        id: Идентификатор записи о платеже.
        yookassa_payment_id: Уникальный ID платежа в системе YooKassa.
        chat_id: ID чата, для которого приобретается подписка.
        tier: Приобретаемый тариф подписки.
        amount_rub: Сумма платежа в рублях.
        status: Статус платежа (pending, succeeded и т.д.).
        created_at: Время формирования платежа.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    yookassa_payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id")
    )
    tier: Mapped[str] = mapped_column(String(32))
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(precision=10, scale=2))
    status: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )


class PresencePoll(Base):
    """Модель голосования присутствия на паре.

    Attributes:
        chat_id: Идентификатор чата Telegram (часть составного ключа).
        message_id: Идентификатор сообщения с опросом (часть составного ключа).
        here_tg_ids: Список Telegram ID проголосовавших студентов.
    """

    __tablename__ = "presence_polls"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    here_tg_ids: Mapped[list[int]] = mapped_column(JSON, default=list)


class SwapRequest(Base):
    """Модель заявки на обмен местами в очереди.

    Attributes:
        id: Идентификатор заявки.
        chat_id: ID чата.
        queue_message_id: ID сообщения очереди.
        subject_id: Внешний ключ предмета.
        from_tg_id: Telegram ID инициатора обмена.
        to_tg_id: Telegram ID целевого участника (если адресный обмен).
        status: Статус заявки (open, accepted, rejected).
        swap_message_id: ID сообщения с предложением обмена.
    """

    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    queue_message_id: Mapped[int] = mapped_column(Integer, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id"))
    from_tg_id: Mapped[int] = mapped_column(BigInteger)
    to_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    swap_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SubscriptionRate(Base):
    """Модель тарифной ставки подписки (управляется через БД).

    Позволяет изменять цены без перезапуска сервиса.

    Attributes:
        id: Идентификатор записи.
        tier: Название тарифа (trial, base, supervip).
        amount_rub: Ежемесячная стоимость в рублях.
        description: Описание тарифа для администратора.
        updated_at: Дата последнего изменения ставки.
    """

    __tablename__ = "subscription_rates"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    tier: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    amount_rub: Mapped[str] = mapped_column(
        Numeric(precision=10, scale=2), nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
