"""Тесты для репозитория чатов и подписок (chat.py)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.constants import TRIAL_DAYS
from src.db.init_db import PaymentRecord
from src.db.repositories.chat import (
    apply_paid_subscription,
    ensure_chat,
    get_chat,
    list_all_chats,
    set_chat_autoclose_rules,
)
from src.services.billing_constants import SUBSCRIPTION_RATES, SubscriptionTier


@pytest.mark.asyncio
async def test_ensure_chat_creates_new(async_session: AsyncSession) -> None:
    """Проверяет создание нового чата и назначение триала."""
    chat = await ensure_chat(async_session, 12345, "Test Chat")
    assert chat.chat_id == 12345
    assert chat.title == "Test Chat"
    assert chat.subscription_tier == SubscriptionTier.TRIAL.value
    assert chat.subscription_ends_at is None

    now = datetime.now(UTC)
    assert chat.trial_ends_at is not None

    trial_ends = chat.trial_ends_at
    if trial_ends.tzinfo is None:
        trial_ends = trial_ends.replace(tzinfo=UTC)
    delta = trial_ends - now
    assert (TRIAL_DAYS - 1) <= delta.days <= TRIAL_DAYS


@pytest.mark.asyncio
async def test_get_chat(async_session: AsyncSession) -> None:
    """Проверяет получение чата по ID."""
    assert await get_chat(async_session, 12345) is None
    await ensure_chat(async_session, 12345, "Test Chat")
    chat = await get_chat(async_session, 12345)
    assert chat is not None
    assert chat.chat_id == 12345


@pytest.mark.asyncio
async def test_ensure_chat_updates_title(async_session: AsyncSession) -> None:
    """Проверяет обновление заголовка существующего чата."""
    await ensure_chat(async_session, 12345, "Old Title")
    chat = await ensure_chat(async_session, 12345, "New Title")
    assert chat.title == "New Title"


@pytest.mark.asyncio
async def test_list_all_chats_pagination(async_session: AsyncSession) -> None:
    """Проверяет пагинацию при получении списка всех чатов."""
    for i in range(5):
        await ensure_chat(async_session, 1000 + i, f"Chat {i}")

    all_chats = await list_all_chats(async_session)
    assert len(all_chats) == 5

    paginated = await list_all_chats(async_session, limit=2, offset=1)
    assert len(paginated) == 2
    assert paginated[0].chat_id == 1001
    assert paginated[1].chat_id == 1002


@pytest.mark.asyncio
async def test_set_chat_autoclose_rules(async_session: AsyncSession) -> None:
    """Проверяет установку правил автозакрытия."""
    rules = [{"time": "22:00", "action": "close"}]
    await set_chat_autoclose_rules(async_session, 12345, rules)

    chat = await get_chat(async_session, 12345)
    assert chat is not None
    assert chat.autoclose_rules == rules


@pytest.mark.asyncio
async def test_apply_paid_subscription_idempotency(
    async_session: AsyncSession,
) -> None:
    """Проверяет идемпотентность apply_paid_subscription."""
    # 1. Создаем чат
    await ensure_chat(async_session, 12345, "Test Chat")

    amount_paid = float(SUBSCRIPTION_RATES[SubscriptionTier.BASE])

    # 2. Создаем платеж в БД с заполненным details
    payment = PaymentRecord(
        yookassa_payment_id="pay_123",
        chat_id=12345,
        tier=SubscriptionTier.BASE.value,
        amount_rub=f"{amount_paid:.2f}",
        status="succeeded",
        details={"audit": "already applied"},
    )
    async_session.add(payment)
    await async_session.flush()

    # 3. Пытаемся применить платеж повторно
    res = await apply_paid_subscription(
        async_session,
        chat_id=12345,
        tier=SubscriptionTier.BASE.value,
        amount_paid=amount_paid,
        months=1,
        payment_record_id="pay_123",
    )

    # 4. Проверяем, что платеж пропущен
    assert res is None


@pytest.mark.asyncio
async def test_apply_paid_subscription_success(
    async_session: AsyncSession,
) -> None:
    """Проверяет успешное применение оплаченной подписки."""
    await ensure_chat(async_session, 12345, "Test Chat")

    amount_paid = float(SUBSCRIPTION_RATES[SubscriptionTier.SUPERVIP])

    # Создаем пустой платеж
    payment = PaymentRecord(
        yookassa_payment_id="pay_999",
        chat_id=12345,
        tier=SubscriptionTier.SUPERVIP.value,
        amount_rub=f"{amount_paid:.2f}",
        status="succeeded",
    )
    async_session.add(payment)
    await async_session.flush()

    res = await apply_paid_subscription(
        async_session,
        chat_id=12345,
        tier=SubscriptionTier.SUPERVIP.value,
        amount_paid=amount_paid,
        months=1,
        payment_record_id="pay_999",
    )

    assert res is not None
    assert res.new_tier == SubscriptionTier.SUPERVIP.value

    chat = await get_chat(async_session, 12345)
    assert chat is not None
    assert chat.subscription_tier == SubscriptionTier.SUPERVIP.value
    assert chat.subscription_ends_at is not None

    await async_session.refresh(payment)
    assert payment.details is not None
    amount_val = payment.details.get("amount_paid")
    assert amount_val is not None
    assert float(str(amount_val)) == amount_paid
