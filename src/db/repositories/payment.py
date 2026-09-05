"""Репозиторий для работы с платежами (PaymentRecord)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import PaymentRecord

logger = logging.getLogger(__name__)


async def get_payment_by_yookassa_id(
    db: AsyncSession, yookassa_payment_id: str
) -> PaymentRecord | None:
    """Возвращает запись о платеже по ID ЮKassa.

    Args:
        db: Асинхронная сессия БД.
        yookassa_payment_id: ID платежа ЮKassa.

    Returns:
        PaymentRecord | None: Объект платежа, если найден.
    """
    stmt = select(PaymentRecord).where(
        PaymentRecord.yookassa_payment_id == yookassa_payment_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_payment_record(
    db: AsyncSession,
    yookassa_id: str,
    chat_id: int,
    tier: str,
    amount_rub: Decimal | str,
    status: str,
    *,
    details: dict[str, Any] | None = None,
    flush: bool = True,
) -> PaymentRecord:
    """Создает запись о платеже.

    Args:
        db: Асинхронная сессия БД.
        yookassa_id: ID платежа ЮKassa.
        chat_id: Telegram ID чата.
        tier: Название тарифа.
        amount_rub: Сумма в рублях.
        status: Статус платежа.
        details: Дополнительные детали аудита расчета.
        flush: Выполнять ли flush/refresh.

    Returns:
        PaymentRecord: Созданная запись.
    """
    amount_decimal = (
        amount_rub if isinstance(amount_rub, Decimal) else Decimal(amount_rub)
    )
    payment_record = PaymentRecord(
        yookassa_payment_id=yookassa_id,
        chat_id=chat_id,
        tier=tier,
        amount_rub=amount_decimal,
        status=status,
        details=details,
    )
    db.add(payment_record)
    if flush:
        await db.flush()
        await db.refresh(payment_record)
    return payment_record
