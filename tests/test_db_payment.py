"""Тесты для репозитория платежей (payment.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.repositories.payment import (
    create_payment_record,
    get_payment_by_yookassa_id,
)
from src.services.billing_constants import SUBSCRIPTION_RATES, SubscriptionTier


@pytest.mark.asyncio
async def test_create_payment_record(async_session: AsyncSession) -> None:
    """Проверяет успешное создание записи о платеже."""
    amount_paid = SUBSCRIPTION_RATES[SubscriptionTier.BASE]
    details = {"gateway": "yookassa", "test": True}

    payment = await create_payment_record(
        async_session,
        yookassa_id="pay_test_001",
        chat_id=10001,
        tier=SubscriptionTier.BASE.value,
        amount_rub=amount_paid,
        status="pending",
        details=details,
        flush=True,
    )

    assert payment is not None
    assert payment.yookassa_payment_id == "pay_test_001"
    assert payment.chat_id == 10001
    assert payment.tier == SubscriptionTier.BASE.value
    assert payment.amount_rub == amount_paid
    assert payment.status == "pending"
    assert payment.details == details


@pytest.mark.asyncio
async def test_get_payment_by_yookassa_id(async_session: AsyncSession) -> None:
    """Проверяет получение платежа по ID Yookassa."""

    not_found = await get_payment_by_yookassa_id(
        async_session, "non_existent_id"
    )
    assert not_found is None

    amount_paid = SUBSCRIPTION_RATES[SubscriptionTier.SUPERVIP]
    await create_payment_record(
        async_session,
        yookassa_id="pay_test_002",
        chat_id=10002,
        tier=SubscriptionTier.SUPERVIP.value,
        amount_rub=amount_paid,
        status="succeeded",
        flush=True,
    )

    fetched = await get_payment_by_yookassa_id(async_session, "pay_test_002")
    assert fetched is not None
    assert fetched.yookassa_payment_id == "pay_test_002"
    assert fetched.chat_id == 10002
    assert fetched.tier == SubscriptionTier.SUPERVIP.value
