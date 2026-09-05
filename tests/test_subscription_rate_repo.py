"""Тесты для репозитория тарифных ставок подписок (subscription_rate.py)."""

from unittest.mock import MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import SubscriptionRate
from src.db.repositories.subscription_rate import (
    get_subscription_rates,
    seed_subscription_rates,
)
from src.services.billing_constants import SUBSCRIPTION_RATES


async def test_seed_and_get_subscription_rates(
    async_session: AsyncSession,
) -> None:
    """Проверяет начальный сидинг тарифов и их загрузку
    через get_subscription_rates.
    """
    await seed_subscription_rates(async_session)
    rates = await get_subscription_rates(async_session)

    for tier, expected_amount in SUBSCRIPTION_RATES.items():
        assert tier in rates
        assert rates[tier] == expected_amount


async def test_seed_subscription_rates_idempotence(
    async_session: AsyncSession,
) -> None:
    """Проверяет повторный вызов seed_subscription_rates (идемпотентность)."""
    await seed_subscription_rates(async_session)
    await seed_subscription_rates(async_session)

    result = await async_session.execute(select(SubscriptionRate))
    rows = result.scalars().all()
    assert len(rows) == len(SUBSCRIPTION_RATES)


async def test_get_subscription_rates_skips_invalid_tier(
    async_session: AsyncSession,
) -> None:
    """Проверяет, что get_subscription_rates пропускает
    некорректные или неизвестные тарифы.
    """

    invalid_rate = SubscriptionRate(
        tier="unknown_super_tier",
        amount_rub="999.00",
        description="Invalid tier test",
    )
    async_session.add(invalid_rate)
    await async_session.flush()

    rates = await get_subscription_rates(async_session)
    assert "unknown_super_tier" not in rates


async def test_seed_subscription_rates_fallback_branch(
    async_session: AsyncSession,
) -> None:
    """Проверяет выполнение fallback-ветки (case _:) с точечными savepoint."""

    mock_bind = MagicMock()
    mock_bind.dialect.name = "unknown_db"

    with patch.object(async_session, "bind", mock_bind):
        await seed_subscription_rates(async_session)

    rates = await get_subscription_rates(async_session)
    assert len(rates) == len(SUBSCRIPTION_RATES)
