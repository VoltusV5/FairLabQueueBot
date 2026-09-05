"""Тесты сервиса биллинга подписок."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dateutil.relativedelta import relativedelta
from src.db.repositories.chat import apply_paid_subscription, ensure_chat
from src.db.repositories.payment import create_payment_record
from src.services.billing_constants import SUBSCRIPTION_RATES, SubscriptionTier
from src.services.subscription_billing import (
    BillingContext,
    _round_money,
    calculate_subscription_upgrade,
)

RATE_BASE = SUBSCRIPTION_RATES[SubscriptionTier.BASE]
RATE_SUPERVIP = SUBSCRIPTION_RATES[SubscriptionTier.SUPERVIP]


def _make_context(
    current_tier: str,
    target_tier: str,
    amount_paid: Decimal,
    months: int = 1,
    upgrade_mode: str = "convert_now",
    now: datetime | None = None,
    sub_end: datetime | None = None,
    trial_end: datetime | None = None,
) -> BillingContext:
    if now is None:
        now = datetime.now(UTC)
    return BillingContext(
        current_tier=current_tier,
        target_tier=target_tier,
        amount_paid=amount_paid,
        months=months,
        upgrade_mode=upgrade_mode,
        now=now,
        sub_end=sub_end,
        trial_end=trial_end,
    )


def test_upgrade_from_trial():
    """Тест покупки подписки во время Trial (от текущего времени)."""
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    ctx = _make_context(
        current_tier="trial",
        target_tier="base",
        amount_paid=RATE_BASE,
        now=now,
        trial_end=now + timedelta(days=20),
    )

    res = calculate_subscription_upgrade(ctx)

    assert res.new_tier == "base"
    assert res.new_deadline == now + relativedelta(months=1)
    assert res.audit_details["scenario"] == "upgrade_from_trial"
    assert res.audit_details["previous_tier"] == "trial"


def test_same_tier_extension():
    """Тест продления тарифа Base (добавляет время к концу подписки)."""
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    existing_end = now + timedelta(days=10)
    ctx = _make_context(
        current_tier="base",
        target_tier="base",
        amount_paid=RATE_BASE,
        now=now,
        sub_end=existing_end,
    )

    res = calculate_subscription_upgrade(ctx)

    assert res.new_tier == "base"
    assert res.new_deadline == existing_end + relativedelta(months=1)
    assert res.audit_details["scenario"] == "same_tier_extension"


def test_upgrade_base_to_supervip_convert_now():
    """Тест Pro-rata конвертации остатка Base при переходе на SuperVIP."""
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    existing_end = now + timedelta(days=15)
    ctx = _make_context(
        current_tier="base",
        target_tier="supervip",
        amount_paid=RATE_SUPERVIP,
        now=now,
        sub_end=existing_end,
    )

    res = calculate_subscription_upgrade(ctx)

    assert res.new_tier == "supervip"
    assert res.audit_details["scenario"] == "upgrade_convert_now_prorate"
    assert res.audit_details["remaining_days"] == "15.00"
    actual_payment_str = str(_round_money(RATE_SUPERVIP))
    assert res.audit_details["actual_payment"] == actual_payment_str
    base_deadline = now + relativedelta(months=1)
    assert res.new_deadline > base_deadline
    assert res.new_deadline < base_deadline + timedelta(days=12)


def test_upgrade_base_to_supervip_deferred():
    """Тест отсроченного запуска SuperVIP (после окончания Base)."""
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    existing_end = now + timedelta(days=10)
    ctx = _make_context(
        current_tier="base",
        target_tier="supervip",
        amount_paid=RATE_SUPERVIP,
        upgrade_mode="deferred",
        now=now,
        sub_end=existing_end,
    )

    res = calculate_subscription_upgrade(ctx)

    assert res.new_tier == "base"
    assert res.pending_tier == "supervip"
    assert res.pending_tier_activates_at == existing_end
    assert res.new_deadline == existing_end + relativedelta(months=1)
    assert res.audit_details["scenario"] == "upgrade_deferred"


@pytest.mark.asyncio
async def test_apply_paid_subscription_with_audit(async_session):
    """Интеграционный тест: детали аудита в PaymentRecord."""
    db = async_session
    chat_id = 999123

    chat = await ensure_chat(db, chat_id, title="Test Audit Chat")
    payment = await create_payment_record(
        db=db,
        yookassa_id="payment_test_123",
        chat_id=chat_id,
        tier="supervip",
        amount_rub=str(RATE_SUPERVIP),
        status="succeeded",
    )

    await apply_paid_subscription(
        db=db,
        chat_id=chat_id,
        tier="supervip",
        amount_paid=float(RATE_SUPERVIP),
        months=1,
        payment_record_id="payment_test_123",
    )

    assert chat.subscription_tier == "supervip"
    assert payment.details is not None
    assert payment.details["scenario"] in (
        "upgrade_from_trial",
        "same_tier_extension",
    )


def test_invalid_target_tier_raises_value_error():
    """Тест: передача неизвестного тарифа вызывает ValueError."""
    ctx = _make_context(
        current_tier="trial",
        target_tier="invalid_tier",
        amount_paid=Decimal("100.0"),
    )
    with pytest.raises(
        ValueError, match="Unsupported target subscription tier"
    ):
        calculate_subscription_upgrade(ctx)


def test_zero_amount_and_months_raises_value_error():
    """Тест: передача нулей в сумме и месяцах вызывает ValueError."""
    ctx = _make_context(
        current_tier="trial",
        target_tier="base",
        amount_paid=Decimal("0.0"),
        months=0,
    )
    with pytest.raises(ValueError, match="strictly greater than 0"):
        calculate_subscription_upgrade(ctx)


def test_negative_amount_or_months_raises_value_error():
    """Тест: передача отрицательных значений вызывает ValueError."""
    ctx = _make_context(
        current_tier="trial",
        target_tier="base",
        amount_paid=Decimal("-10.0"),
        months=1,
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_subscription_upgrade(ctx)


def test_target_trial_tier_raises_value_error():
    """Тест: передача target_tier='trial' вызывает ValueError."""
    ctx = _make_context(
        current_tier="base",
        target_tier="trial",
        amount_paid=Decimal("100.0"),
    )
    err_pattern = "Cannot upgrade or switch to TRIAL tier"
    with pytest.raises(ValueError, match=err_pattern):
        calculate_subscription_upgrade(ctx)


def test_invalid_current_tier_raises_value_error():
    """Тест: передача невалидного current_tier вызывает ValueError."""
    ctx = _make_context(
        current_tier="invalid_tier",
        target_tier="base",
        amount_paid=Decimal("100.0"),
    )
    err_pattern = "Unsupported current subscription tier"
    with pytest.raises(ValueError, match=err_pattern):
        calculate_subscription_upgrade(ctx)


def test_deferred_downgrade_supervip_to_base():
    """Тест: отложенный downgrade с SuperVIP на Base.

    При downgrade (supervip -> base) тариф остаётся supervip до окончания
    текущей подписки, а new_tier = supervip, pending_tier = base.
    """
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    sub_end = now + timedelta(days=20)
    ctx = _make_context(
        current_tier="supervip",
        target_tier="base",
        amount_paid=RATE_BASE,
        upgrade_mode="convert_now",
        now=now,
        sub_end=sub_end,
    )

    res = calculate_subscription_upgrade(ctx)

    assert res.new_tier == "supervip"
    assert res.pending_tier == "base"
    assert res.pending_tier_activates_at == sub_end
    assert res.new_deadline == sub_end + relativedelta(months=1)
    assert res.audit_details["scenario"] == "downgrade_deferred"


def test_custom_rates_injected_into_calculate():
    """Тест: расчет корректно принимает внешние ставки."""
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    from src.services.billing_constants import SubscriptionTier

    custom_rates = {
        SubscriptionTier.TRIAL: Decimal("0.00"),
        SubscriptionTier.BASE: Decimal("200.00"),
        SubscriptionTier.SUPERVIP: Decimal("400.00"),
    }
    ctx = _make_context(
        current_tier="trial",
        target_tier="base",
        amount_paid=Decimal("200.0"),
        now=now,
    )

    res = calculate_subscription_upgrade(ctx, rates=custom_rates)

    assert res.new_tier == "base"
    assert res.new_deadline == now + relativedelta(months=1)


@pytest.mark.asyncio
async def test_apply_paid_subscription_idempotency(async_session):
    """Тест: повторный вызов apply_paid_subscription — no-op."""
    db = async_session
    chat_id = 999124

    await ensure_chat(db, chat_id, title="Idempotency Test Chat")
    await create_payment_record(
        db=db,
        yookassa_id="payment_idem_001",
        chat_id=chat_id,
        tier="base",
        amount_rub=str(RATE_BASE),
        status="succeeded",
    )

    # Первый вызов — должен применить подписку
    result1 = await apply_paid_subscription(
        db=db,
        chat_id=chat_id,
        tier="base",
        amount_paid=float(RATE_BASE),
        months=1,
        payment_record_id="payment_idem_001",
    )
    assert result1 is not None
    assert result1.new_tier == "base"

    # Второй вызов с тем же payment_id — должен вернуть None
    result2 = await apply_paid_subscription(
        db=db,
        chat_id=chat_id,
        tier="base",
        amount_paid=float(RATE_BASE),
        months=1,
        payment_record_id="payment_idem_001",
    )
    assert result2 is None
