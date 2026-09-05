"""Репозиторий для работы с чатами и подписками."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db.constants import DEFAULT_PAGINATION_LIMIT, TRIAL_DAYS
from src.db.init_db import Chat
from src.db.repositories.payment import get_payment_by_yookassa_id
from src.db.repositories.subscription_rate import get_subscription_rates
from src.services.billing_constants import STATUS_EXPIRED, SubscriptionTier
from src.services.subscription_billing import (
    BillingContext,
    BillingResult,
    calculate_subscription_upgrade,
)

logger = logging.getLogger(__name__)


async def get_chat(db: AsyncSession, chat_id: int) -> Chat | None:
    """Возвращает чат по его ID.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.

    Returns:
        Chat | None: Объект чата, если найден, иначе None.
    """
    stmt = select(Chat).where(Chat.chat_id == chat_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def ensure_chat(
    db: AsyncSession, chat_id: int, title: str | None = None
) -> Chat:
    """Гарантирует существование чата в БД.

    При создании нового чата выдается пробный период.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        title: Название чата.

    Returns:
        Chat: Объект чата.
    """
    chat = await get_chat(db, chat_id)

    if chat is None:
        now = datetime.now(UTC)
        chat = Chat(
            chat_id=chat_id,
            title=title,
            subscription_tier=SubscriptionTier.TRIAL.value,
            trial_ends_at=now + timedelta(days=TRIAL_DAYS),
            subscription_ends_at=None,
            autoclose_enabled=False,
        )
        db.add(chat)
        await db.flush()
        await db.refresh(chat)
        return chat

    if title and chat.title != title:
        chat.title = title
        await db.flush()
        await db.refresh(chat)
    return chat


async def list_all_chats(
    db: AsyncSession, limit: int = DEFAULT_PAGINATION_LIMIT, offset: int = 0
) -> Sequence[Chat]:
    """Возвращает список всех чатов.

    Args:
        db: Асинхронная сессия БД.
        limit: Лимит записей.
        offset: Смещение записей.

    Returns:
        Sequence[Chat]: Список объектов чатов.
    """
    stmt = select(Chat).order_by(Chat.chat_id).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def set_chat_autoclose_rules(
    db: AsyncSession,
    chat_id: int,
    rules: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Устанавливает правила автозакрытия для чата.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        rules: Список правил автозакрытия.
    """
    chat = await ensure_chat(db, chat_id)
    chat.autoclose_rules = (
        [dict(r) for r in rules] if rules is not None else None
    )
    await db.flush()


def _subscription_access_state(
    chat: Chat, now: datetime
) -> tuple[str, datetime | None]:
    """Возвращает текущий статус доступа и дату окончания.

    Args:
        chat: Объект чата.
        now: Текущее время.

    Returns:
        tuple[str, datetime | None]: Статус подписки и время окончания.
    """
    tier = (chat.subscription_tier or SubscriptionTier.TRIAL.value).lower()
    sub_end = chat.subscription_ends_at
    trial_end = chat.trial_ends_at

    if tier in SubscriptionTier.paid_tiers() and sub_end and sub_end > now:
        return tier, sub_end
    if trial_end and trial_end > now:
        return SubscriptionTier.TRIAL.value, trial_end
    return STATUS_EXPIRED, None


def _stack_calendar_months_for_purchase(target_tier: str, state: str) -> bool:
    """Проверяет возможность календарного суммирования дней подписки.

    Args:
        target_tier: Целевой тариф.
        state: Текущий статус.

    Returns:
        bool: True, если возможно суммирование месяцев, иначе False.
    """
    normalized_target_tier = target_tier.lower()
    if normalized_target_tier in SubscriptionTier.paid_tiers():
        return state in (
            normalized_target_tier,
            SubscriptionTier.TRIAL.value,
            STATUS_EXPIRED,
        )
    return False


async def apply_paid_subscription(
    db: AsyncSession,
    chat_id: int,
    tier: str,
    amount_paid: float,
    months: int = 0,
    *,
    upgrade_mode: Literal["convert_now", "deferred"] = "convert_now",
    payment_record_id: str | None = None,
    commit: bool = True,
) -> BillingResult | None:
    """Применяет оплаченную подписку к чату.

    Если платёж с данным payment_record_id уже имеет заполненные details,
    операция повторно не применяется.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        tier: Название тарифа.
        amount_paid: Сумма оплаты.
        months: Количество месяцев.
        upgrade_mode: Режим апгрейда ('convert_now' или 'deferred').
        payment_record_id: ID записи платежа ЮКасса для сохранения аудита.
        commit: Выполнять ли commit/flush.

    Returns:
        BillingResult: Результат расчета биллинга.
    """
    existing_payment = None
    if payment_record_id:
        existing_payment = await get_payment_by_yookassa_id(
            db, payment_record_id
        )
        if existing_payment and existing_payment.details is not None:
            logger.warning(
                "Duplicate webhook for payment '%s' — skipping re-apply.",
                payment_record_id,
            )
            return None

    chat = await ensure_chat(db, chat_id)
    now = datetime.now(UTC)

    rates = await get_subscription_rates(db)

    ctx = BillingContext(
        current_tier=chat.subscription_tier or SubscriptionTier.TRIAL.value,
        target_tier=tier,
        amount_paid=Decimal(str(amount_paid)),
        months=months,
        upgrade_mode=upgrade_mode,
        now=now,
        sub_end=chat.subscription_ends_at,
        trial_end=chat.trial_ends_at,
    )

    billing_res = calculate_subscription_upgrade(ctx, rates=rates)

    chat.subscription_tier = billing_res.new_tier
    chat.subscription_ends_at = billing_res.new_deadline
    chat.pending_tier = billing_res.pending_tier
    chat.pending_tier_activates_at = billing_res.pending_tier_activates_at
    chat.subscription_reminder_state = None

    if payment_record_id and existing_payment:
        existing_payment.details = billing_res.audit_details
        flag_modified(existing_payment, "details")

    if commit:
        await db.flush()

    return billing_res


async def is_chat_admin(db: AsyncSession, chat_id: int, tg_id: int) -> bool:
    """Проверяет, является ли пользователь старостой (администратором) чата.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        tg_id: Telegram ID пользователя.

    Returns:
        bool: True, если пользователь в списке старост, иначе False.
    """
    chat = await get_chat(db, chat_id)
    if not chat or not chat.admins:
        return False
    return tg_id in chat.admins


async def add_chat_admin(db: AsyncSession, chat_id: int, tg_id: int) -> bool:
    """Добавляет пользователя в список старост (администраторов) чата.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        tg_id: Telegram ID пользователя.

    Returns:
        bool: True, если добавлен, False если уже был старостой.
    """
    chat = await ensure_chat(db, chat_id)
    admins = list(chat.admins or [])
    if tg_id in admins:
        return False
    admins.append(tg_id)
    chat.admins = admins
    flag_modified(chat, "admins")
    await db.flush()
    return True


async def remove_chat_admin(
    db: AsyncSession, chat_id: int, tg_id: int
) -> bool:
    """Удаляет пользователя из списка старост чата.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        tg_id: Telegram ID пользователя.

    Returns:
        bool: True, если удален, False если его не было в списке.
    """
    chat = await ensure_chat(db, chat_id)
    admins = list(chat.admins or [])
    if tg_id not in admins:
        return False
    admins.remove(tg_id)
    chat.admins = admins
    flag_modified(chat, "admins")
    await db.flush()
    return True
