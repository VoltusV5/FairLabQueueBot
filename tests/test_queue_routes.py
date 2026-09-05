"""Тесты для обработчиков очередей (src/handlers/queue_routes.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.handlers import queue_common as qc
from src.handlers import queue_routes


def _create_message(
    text: str,
    chat_id: int = 100,
    message_id: int = 1,
    user_id: int = 1001,
    reply_to: Message | None = None,
) -> Message:
    """Создает мок объекта aiogram Message."""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = message_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.chat.title = "Тестовый чат"
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.from_user.username = "testuser"
    msg.from_user.full_name = "Test User"
    msg.reply_to_message = reply_to

    sent_mock = MagicMock(spec=Message)
    sent_mock.message_id = message_id + 100
    sent_mock.edit_reply_markup = AsyncMock()

    msg.answer = AsyncMock(return_value=sent_mock)
    msg.edit_text = AsyncMock()
    msg.edit_reply_markup = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _create_callback(
    data: str | None,
    chat_id: int = 100,
    message_id: int = 1,
    user_id: int = 1001,
    text: str = "Текст сообщения",
) -> CallbackQuery:
    """Создает мок объекта aiogram CallbackQuery."""
    cb = MagicMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = MagicMock(spec=User)
    cb.from_user.id = user_id
    cb.from_user.username = "testuser"
    cb.from_user.full_name = "Test User"

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = message_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.edit_text = AsyncMock()
    msg.edit_reply_markup = AsyncMock()
    msg.delete = AsyncMock()

    cb.message = msg
    cb.answer = AsyncMock()
    cb.bot = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_cmd_queue_no_subscription(
    async_session: AsyncSession,
) -> None:
    """Проверка блокировки команды /queue без активной подписки."""
    db = async_session
    chat = await queries_db.ensure_chat(db, 200, "Тестовый чат")
    chat.subscription_tier = "base"
    chat.subscription_ends_at = datetime(2020, 1, 1)
    chat.trial_ends_at = datetime(2020, 1, 1)
    await db.flush()

    msg = _create_message("/queue Математика 01.09 10:00", chat_id=200)
    await queue_routes.cmd_queue(msg, db)

    msg.answer.assert_awaited_once()
    assert "нет активной подписки" in msg.answer.await_args[0][0]


@pytest.mark.asyncio
async def test_cmd_queue_success(
    async_session: AsyncSession,
) -> None:
    """Проверка успешного создания очереди через /queue."""
    db = async_session
    chat = await queries_db.ensure_chat(db, 300, "Тестовый чат")
    chat.subscription_tier = "base"
    chat.subscription_ends_at = datetime.now(UTC).replace(
        tzinfo=None
    ) + timedelta(days=30)
    await db.flush()

    msg = _create_message(
        "/queue Физика 01.09 10:00", chat_id=300, message_id=10
    )
    await queue_routes.cmd_queue(msg, db)

    msg.answer.assert_awaited_once()
    q = await queries_db.get_queue_by_chat_message(db, 300, 110)
    assert q is not None
    assert q.status == qc.QueueStatus.WAITING_FOR_PARTICIPANTS


@pytest.mark.asyncio
async def test_cb_participate_add_and_remove(
    async_session: AsyncSession,
) -> None:
    """Проверка записи и отмены участия в очереди."""
    db = async_session
    chat_id, msg_id = 400, 888
    user_id = 5001

    subj = await queries_db.get_or_create_subject(db, chat_id, "Химия")
    await queries_db.add_queue_row(
        db,
        subject_id=subj.id,
        chat_id=chat_id,
        message_id=msg_id,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants=[],
    )

    cb_add = _create_callback(
        f"pa|{chat_id}|{msg_id}", chat_id, msg_id, user_id
    )
    await queue_routes.cb_participate_add(cb_add, db)
    cb_add.answer.assert_awaited_with("Вы записаны ✅")

    q = await queries_db.get_queue_by_chat_message(db, chat_id, msg_id)
    assert q is not None
    assert user_id in (q.participants or [])

    # Отмена участия
    cb_rem = _create_callback(
        f"pr|{chat_id}|{msg_id}", chat_id, msg_id, user_id
    )
    await queue_routes.cb_participate_remove(cb_rem, db)
    cb_rem.answer.assert_awaited_with("Участие отменено ❌")

    q2 = await queries_db.get_queue_by_chat_message(db, chat_id, msg_id)
    assert q2 is not None
    assert user_id not in (q2.participants or [])


@pytest.mark.asyncio
async def test_cb_del_yes(
    async_session: AsyncSession,
) -> None:
    """Проверка удаления очереди по подтверждению."""
    db = async_session
    chat_id, msg_id = 500, 777
    bot = AsyncMock()

    subj = await queries_db.get_or_create_subject(db, chat_id, "Биология")
    await queries_db.add_queue_row(
        db,
        subject_id=subj.id,
        chat_id=chat_id,
        message_id=msg_id,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants=[],
    )

    cb = _create_callback(f"dqy|{chat_id}|{msg_id}", chat_id, msg_id)
    await queue_routes.cb_del_yes(cb, bot, db)

    q = await queries_db.get_queue_by_chat_message(db, chat_id, msg_id)
    assert q is None
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_add_end(
    async_session: AsyncSession,
) -> None:
    """Проверка добавления в конец сформированной очереди."""
    db = async_session
    chat_id, msg_id = 600, 666
    user_id = 6001

    subj = await queries_db.get_or_create_subject(db, chat_id, "История")
    await queries_db.add_queue_row(
        db,
        subject_id=subj.id,
        chat_id=chat_id,
        message_id=msg_id,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[6002],
    )

    bot = AsyncMock()
    cb = _create_callback(f"ae|{chat_id}|{msg_id}", chat_id, msg_id, user_id)
    with patch(
        "src.handlers.queue_common.refresh_queue_message",
        new_callable=AsyncMock,
    ) as mock_refresh:
        await queue_routes.cb_add_end(cb, bot, db)
        cb.answer.assert_awaited_with("Вы в конце списка ✅")
        mock_refresh.assert_awaited_once()

    q = await queries_db.get_queue_by_chat_message(db, chat_id, msg_id)
    assert q is not None
    assert q.participants == [6002, user_id]


@pytest.mark.asyncio
async def test_swap_request_and_accept(
    async_session: AsyncSession,
) -> None:
    """Проверка создания и подтверждения взаимного обмена местами."""
    db = async_session
    chat_id, msg_id = 700, 555
    u1, u2 = 7001, 7002
    bot = AsyncMock()

    subj = await queries_db.get_or_create_subject(db, chat_id, "Экономика")
    await queries_db.add_queue_row(
        db,
        subject_id=subj.id,
        chat_id=chat_id,
        message_id=msg_id,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants=[u1, u2],
    )

    cb1 = _create_callback(f"sw|{chat_id}|{msg_id}", chat_id, msg_id, u1)
    sent_mock = MagicMock(spec=Message)
    sent_mock.message_id = 12345
    bot.send_message = AsyncMock(return_value=sent_mock)

    await queue_routes.cb_swap_request(cb1, bot, db)
    cb1.answer.assert_awaited_with("Заявка создана")

    # Второй пользователь принимает обмен
    cb2 = _create_callback(f"sw|{chat_id}|{msg_id}", chat_id, msg_id, u2)
    with patch(
        "src.handlers.queue_common.refresh_queue_message",
        new_callable=AsyncMock,
    ) as mock_refresh:
        await queue_routes.cb_swap_request(cb2, bot, db)
        cb2.answer.assert_awaited_with("Места поменяны ✅")
        mock_refresh.assert_awaited_once()

    q = await queries_db.get_queue_by_chat_message(db, chat_id, msg_id)
    assert q is not None
    assert q.participants == [u2, u1]
