"""Репозиторий для работы с тарифными ставками подписок."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import SubscriptionRate
from src.services.billing_constants import SUBSCRIPTION_RATES, SubscriptionTier

logger = logging.getLogger(__name__)


async def get_subscription_rates(
    db: AsyncSession,
) -> dict[SubscriptionTier, Decimal]:
    """Загружает актуальные тарифные ставки из БД.

    Args:
        db: Асинхронная сессия БД.

    Returns:
        Словарь {SubscriptionTier: ставка в Decimal}.
    """
    result = await db.execute(select(SubscriptionRate))
    rows = result.scalars().all()

    rates: dict[SubscriptionTier, Decimal] = {}
    for row in rows:
        try:
            tier = SubscriptionTier(row.tier)
            rates[tier] = Decimal(str(row.amount_rub))
        except (ValueError, InvalidOperation):
            logger.warning(
                "Invalid tier or amount in subscription_rates: "
                "tier='%s', amount_rub='%s'",
                row.tier,
                row.amount_rub,
            )

    return rates


async def seed_subscription_rates(db: AsyncSession) -> None:
    """Заполняет таблицу ставок начальными значениями из billing_constants.

    Использует db.flush().

    Args:
        db: Асинхронная сессия БД.
    """
    values = [
        {
            "tier": tier.value,
            "amount_rub": str(amount),
            "description": f"Default rate for {tier.value} tier",
        }
        for tier, amount in SUBSCRIPTION_RATES.items()
    ]
    if not values:
        return

    try:
        bind = db.bind
        dialect_name = bind.dialect.name if bind else ""
    except Exception:
        dialect_name = ""

    match dialect_name:
        case "postgresql":
            pg_stmt = (
                pg_insert(SubscriptionRate)
                .values(values)
                .on_conflict_do_nothing(index_elements=["tier"])
            )
            await db.execute(pg_stmt)
            await db.flush()
            logger.info("subscription_rates seeded via PostgreSQL ON CONFLICT")
        case "sqlite":
            sqlite_stmt = (
                sqlite_insert(SubscriptionRate)
                .values(values)
                .on_conflict_do_nothing(index_elements=["tier"])
            )
            await db.execute(sqlite_stmt)
            await db.flush()
            logger.info("subscription_rates seeded via SQLite ON CONFLICT")
        case _:
            for val in values:
                try:
                    async with db.begin_nested():
                        db.add(SubscriptionRate(**val))
                        await db.flush()
                except IntegrityError:
                    logger.debug(
                        "Rate '%s' already exists, skipping", val["tier"]
                    )
            logger.info("subscription_rates seeded via fallback savepoints")
