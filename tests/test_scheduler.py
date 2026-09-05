"""Тесты для фонового планировщика задач (src/services/scheduler.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.handlers import queue_common as qc
from src.services import scheduler


@pytest.mark.asyncio
async def test_apply_pending_tiers_activated(
    async_session: AsyncSession,
) -> None:
    """Проверка активации отложенного тарифа при наступлении даты."""
    db = async_session
    chat_id = 100
    now = datetime.now(UTC)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    chat = await queries_db.ensure_chat(db, chat_id, "Чат планировщика")
    chat.subscription_tier = "base"
    chat.pending_tier = "supervip"
    chat.pending_tier_activates_at = now - timedelta(hours=1)
    await db.flush()

    await scheduler._apply_pending_tiers(bot, db, now)

    updated_chat = await queries_db.get_chat(db, chat_id)
    assert updated_chat is not None
    assert updated_chat.subscription_tier == "supervip"
    assert updated_chat.pending_tier is None
    assert updated_chat.pending_tier_activates_at is None
    bot.send_message.assert_awaited_once()
    assert "активирован" in bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_apply_pending_tiers_future(
    async_session: AsyncSession,
) -> None:
    """Проверка, что отложенный тариф в будущем не активируется."""
    db = async_session
    chat_id = 101
    now = datetime.now(UTC)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    chat = await queries_db.ensure_chat(db, chat_id, "Чат")
    chat.subscription_tier = "base"
    chat.pending_tier = "supervip"
    chat.pending_tier_activates_at = now + timedelta(days=2)
    await db.flush()

    await scheduler._apply_pending_tiers(bot, db, now)

    updated_chat = await queries_db.get_chat(db, chat_id)
    assert updated_chat is not None
    assert updated_chat.subscription_tier == "base"
    assert updated_chat.pending_tier == "supervip"
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscription_reminder_3d(async_session: AsyncSession) -> None:
    """Проверка отправки напоминания за 2-3 дня до окончания подписки."""
    db = async_session
    chat_id = 102
    now = datetime.now(UTC)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    chat = await queries_db.ensure_chat(db, chat_id, "Чат")
    chat.subscription_tier = "base"
    chat.subscription_ends_at = now + timedelta(days=2, hours=10)
    await db.flush()

    await scheduler._maybe_send_subscription_reminder(bot, db, chat, now)

    bot.send_message.assert_awaited_once()
    assert "осталось 2–3 дня" in bot.send_message.await_args.args[1]
    assert chat.subscription_reminder_state.get("d3") is True

    # Идемпотентность: повторный запуск не шлет сообщение второй раз
    bot.send_message.reset_mock()
    await scheduler._maybe_send_subscription_reminder(bot, db, chat, now)
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscription_reminder_1d(async_session: AsyncSession) -> None:
    """Проверка отправки напоминания за сутки до окончания доступа."""
    db = async_session
    chat_id = 103
    now = datetime.now(UTC)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    chat = await queries_db.ensure_chat(db, chat_id, "Чат")
    chat.subscription_tier = "supervip"
    chat.subscription_ends_at = now + timedelta(hours=15)
    await db.flush()

    await scheduler._maybe_send_subscription_reminder(bot, db, chat, now)

    bot.send_message.assert_awaited_once()
    assert "осталось менее суток" in bot.send_message.await_args.args[1]
    assert chat.subscription_reminder_state.get("d1") is True


@pytest.mark.asyncio
async def test_autoclose_queue(async_session: AsyncSession) -> None:
    """Проверка вызова finalize_queue_from_scheduler для закрытия очереди."""
    db = async_session
    chat_id = 104
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Предмет")
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=50,
        subject_id=subj.id,
        lesson_date=now + timedelta(hours=1),
        close_at=now - timedelta(minutes=5),
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value,
        participants=[1, 2],
    )
    await db.flush()

    q = await queries_db.get_queue_by_chat_message(db, chat_id, 50)
    assert q is not None

    with patch(
        "src.handlers.queue_common.finalize_queue_from_scheduler", AsyncMock()
    ) as mock_finalize:
        await scheduler._autoclose_queue(bot, db, q)
        mock_finalize.assert_awaited_once_with(bot, db, q)


@pytest.mark.asyncio
async def test_tick_voting_reminders_and_penalties(
    async_session: AsyncSession,
) -> None:
    """Проверка напоминания через 5ч и начисления штрафов через 24ч."""
    db = async_session
    chat_id = 105
    now = datetime.now(UTC)
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    subj = await queries_db.get_or_create_subject(db, chat_id, "Алгоритмы")

    # 1. Очередь на голосовании 6 часов назад (срабатывает reminder_5h)
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=60,
        subject_id=subj.id,
        lesson_date=now - timedelta(hours=6),
        close_at=now - timedelta(hours=6),
        status=qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value,
        participants=[201, 202],
    )
    q1 = await queries_db.get_queue_by_chat_message(db, chat_id, 60)
    assert q1 is not None
    q1.extra = {"voting_started_at": (now - timedelta(hours=6)).isoformat()}
    await db.flush()

    # 2. Очередь на голосовании 25 часов назад (срабатывает deadline_24h)
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=70,
        subject_id=subj.id,
        lesson_date=now - timedelta(hours=25),
        close_at=now - timedelta(hours=25),
        status=qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value,
        participants=[301, 302],
    )
    q2 = await queries_db.get_queue_by_chat_message(db, chat_id, 70)
    assert q2 is not None
    q2.extra = {
        "voting_started_at": (now - timedelta(hours=25)).isoformat(),
        "reminder_5h_sent": True,
    }
    await db.flush()

    # Подменяем AsyncSessionLocal на сессию теста
    class MockSessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch(
        "src.db.db.AsyncSessionLocal", return_value=MockSessionContext()
    ):
        await scheduler._tick(bot)

    # Проверяем q1: выставлен флаг reminder_5h_sent
    updated_q1 = await queries_db.get_queue_by_chat_message(db, chat_id, 60)
    assert updated_q1 is not None
    assert updated_q1.extra.get("reminder_5h_sent") is True

    # Проверяем q2: выставлен флаг deadline_24h_applied
    updated_q2 = await queries_db.get_queue_by_chat_message(db, chat_id, 70)
    assert updated_q2 is not None
    assert updated_q2.extra.get("deadline_24h_applied") is True


@pytest.mark.asyncio
async def test_run_periodic_resilience() -> None:
    """Проверка перехвата исключений в цикле run_periodic."""
    bot = MagicMock(spec=Bot)
    call_count = 0

    async def fake_tick(b: Bot) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Временная ошибка БД")
        raise asyncio.CancelledError()

    with patch("src.services.scheduler._tick", side_effect=fake_tick):
        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(asyncio.CancelledError):
                await scheduler.run_periodic(bot)

    assert call_count == 2
