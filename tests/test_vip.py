"""Тесты для команд SuperVIP и настроек чата (src/handlers/vip.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, MessageEntity, User
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.handlers import queue_common as qc
from src.handlers import vip


def _create_message(
    text: str,
    chat_id: int = 100,
    message_id: int = 1,
    user_id: int = 1001,
    username: str = "testuser",
    reply_to: Message | None = None,
    entities: list[MessageEntity] | None = None,
) -> Message:
    """Создает мок объекта Message для aiogram 3."""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = message_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.chat.title = "Тестовый чат"
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.full_name = "Test User"
    msg.reply_to_message = reply_to
    msg.entities = entities or []

    sent_mock = MagicMock(spec=Message)
    sent_mock.message_id = message_id + 100
    sent_mock.edit_reply_markup = AsyncMock()

    msg.answer = AsyncMock(return_value=sent_mock)
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_cmd_changename(async_session: AsyncSession) -> None:
    """Проверка смены отображаемого имени через /changename."""
    db = async_session
    user_id = 777
    msg = _create_message(
        "/changename Иван Иванов", user_id=user_id, username="ivan"
    )

    await vip.cmd_changename(msg, db)

    msg.answer.assert_awaited_once()
    assert "обновлено" in msg.answer.await_args.args[0]

    user_display = await queries_db.get_user_display(db, user_id)
    assert "Иван Иванов" in user_display


@pytest.mark.asyncio
async def test_cmd_auto_toggle(async_session: AsyncSession) -> None:
    """Проверка переключения флага автозакрытия чата через /auto."""
    db = async_session
    chat_id = 150
    chat = await queries_db.ensure_chat(db, chat_id, "Группа")
    assert not chat.autoclose_enabled

    msg = _create_message("/auto", chat_id=chat_id)

    # Первое переключение -> включено
    await vip.cmd_auto(msg, db)
    assert chat.autoclose_enabled is True
    assert "включено" in msg.answer.await_args.args[0]

    # Второе переключение -> выключено
    await vip.cmd_auto(msg, db)
    assert chat.autoclose_enabled is False
    assert "выключено" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_closeafter(async_session: AsyncSession) -> None:
    """Проверка команды /closeafter для активной очереди."""
    db = async_session
    chat_id = 200
    mid = 10
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Физика")
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=mid,
        subject_id=subj.id,
        lesson_date=now + timedelta(hours=5),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value,
        participants=[101],
    )
    await db.flush()

    reply_msg = _create_message(
        "Сообщение очереди", chat_id=chat_id, message_id=mid
    )
    cmd_msg = _create_message(
        "/closeafter 30м", chat_id=chat_id, reply_to=reply_msg
    )

    with patch("src.handlers.queue_common.safe_edit_text", AsyncMock()):
        await vip.cmd_closeafter(cmd_msg, db, bot)

    cmd_msg.answer.assert_awaited_once()
    assert "Набор закроется" in cmd_msg.answer.await_args.args[0]

    updated_q = await queries_db.get_queue_by_chat_message(db, chat_id, mid)
    assert updated_q is not None
    assert updated_q.close_at is not None
    assert updated_q.extra.get("manual_closeafter") is True


@pytest.mark.asyncio
async def test_cmd_closebefore_success_and_past(
    async_session: AsyncSession,
) -> None:
    """Проверка команды /closebefore (успех и ошибка при прошлом времени)."""
    db = async_session
    chat_id = 250
    mid = 20
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Математика")
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=mid,
        subject_id=subj.id,
        lesson_date=now + timedelta(hours=2),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value,
        participants=[101],
    )
    await db.flush()

    reply_msg = _create_message("Очередь", chat_id=chat_id, message_id=mid)

    # 1. Успешный перенос дедлайна за 30м до занятия
    cmd_ok = _create_message(
        "/closebefore 30м", chat_id=chat_id, reply_to=reply_msg
    )
    with patch("src.handlers.queue_common.safe_edit_text", AsyncMock()):
        await vip.cmd_closebefore(cmd_ok, db, bot)

    assert "Набор закроется не позже" in cmd_ok.answer.await_args.args[0]

    # 2. Попытка установить дедлайн за 3 часа до занятия (уже прошло)
    cmd_past = _create_message(
        "/closebefore 3ч", chat_id=chat_id, reply_to=reply_msg
    )
    await vip.cmd_closebefore(cmd_past, db, bot)
    assert "в прошлом" in cmd_past.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_closeat(async_session: AsyncSession) -> None:
    """Проверка команды /closeat для установки точного времени закрытия."""
    db = async_session
    chat_id = 300
    mid = 30
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Химия")
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=mid,
        subject_id=subj.id,
        lesson_date=now + timedelta(days=2),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value,
        participants=[101],
    )
    await db.flush()

    reply_msg = _create_message("Очередь", chat_id=chat_id, message_id=mid)
    future_date = (now + timedelta(days=1)).strftime("%d.%m 15:00")
    cmd_msg = _create_message(
        f"/closeat {future_date}", chat_id=chat_id, reply_to=reply_msg
    )

    with patch("src.handlers.queue_common.safe_edit_text", AsyncMock()):
        await vip.cmd_closeat(cmd_msg, db, bot)

    cmd_msg.answer.assert_awaited_once()
    assert "Набор закроется" in cmd_msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_newautorule(async_session: AsyncSession) -> None:
    """Проверка настройки и сброса кастомных правил автозакрытия."""
    db = async_session
    chat_id = 350
    await queries_db.ensure_chat(db, chat_id, "Группа")

    # Установка правил
    cmd_set = _create_message(
        "/newautorule 0-1:n,1-18:1,18-999:15", chat_id=chat_id
    )
    await vip.cmd_newautorule(cmd_set, db)
    assert "Сохранено правил: 3" in cmd_set.answer.await_args.args[0]

    chat = await queries_db.get_chat(db, chat_id)
    assert chat is not None
    assert chat.autoclose_rules is not None
    assert len(chat.autoclose_rules) == 3

    # Сброс правил на дефолтные
    cmd_reset = _create_message("/newautorule default", chat_id=chat_id)
    await vip.cmd_newautorule(cmd_reset, db)
    assert "сброшены" in cmd_reset.answer.await_args.args[0]

    chat_after = await queries_db.get_chat(db, chat_id)
    assert chat_after is not None
    assert chat_after.autoclose_rules is None


@pytest.mark.asyncio
async def test_cmd_group_flow(async_session: AsyncSession) -> None:
    """Проверка команды /group (отказ без SuperVIP и успешное объединение)."""
    db = async_session
    chat_id = 400
    chat = await queries_db.ensure_chat(db, chat_id, "VIP Чат")

    # 1. Отказ без SuperVIP
    chat.subscription_tier = "base"
    chat.subscription_ends_at = datetime.now(UTC) + timedelta(days=10)
    await db.flush()

    msg_deny = _create_message("/group @u1 @u2", chat_id=chat_id)
    await vip.cmd_group(msg_deny, db)
    assert "Нужна подписка SuperVIP" in msg_deny.answer.await_args.args[0]

    # 2. Успешное создание группы с SuperVIP
    chat.subscription_tier = "supervip"
    await db.flush()

    await queries_db.ensure_user(db, 1001, "u1", "Пользователь 1")
    await queries_db.ensure_user(db, 1002, "u2", "Пользователь 2")
    await db.flush()

    msg_ok = _create_message("/group @u1 @u2", chat_id=chat_id)
    await vip.cmd_group(msg_ok, db)

    msg_ok.answer.assert_awaited_once()
    assert "Группа создана" in msg_ok.answer.await_args.args[0]

    updated_chat = await queries_db.get_chat(db, chat_id)
    assert updated_chat is not None
    assert [1001, 1002] in (updated_chat.groups or [])


@pytest.mark.asyncio
async def test_cmd_insert(async_session: AsyncSession) -> None:
    """Проверка команды /insert для сформированной очереди."""
    db = async_session
    chat_id = 450
    mid = 40
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    chat = await queries_db.ensure_chat(db, chat_id, "VIP Чат")
    chat.subscription_tier = "supervip"
    chat.subscription_ends_at = now + timedelta(days=30)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Биология")
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=mid,
        subject_id=subj.id,
        lesson_date=now + timedelta(hours=3),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value,
        participants=[101, 102],
    )
    await queries_db.ensure_user(db, 101, "u1", "Участник 1")
    await queries_db.ensure_user(db, 102, "u2", "Участник 2")
    await queries_db.ensure_user(db, 103, "target_user", "Новый Участник")
    await db.flush()

    reply_msg = _create_message(
        "Сформированная очередь", chat_id=chat_id, message_id=mid
    )
    cmd_msg = _create_message(
        "/insert @target_user 2",
        chat_id=chat_id,
        user_id=101,
        reply_to=reply_msg,
    )

    with patch("src.handlers.queue_common.refresh_queue_message", AsyncMock()):
        await vip.cmd_insert(cmd_msg, db, bot)

    cmd_msg.answer.assert_awaited_once()
    assert "вставил" in cmd_msg.answer.await_args.args[0]

    updated_q = await queries_db.get_queue_by_chat_message(db, chat_id, mid)
    assert updated_q is not None
    assert updated_q.participants == [101, 103, 102]


@pytest.mark.asyncio
async def test_cmd_last_formed(async_session: AsyncSession) -> None:
    """Проверка команды /last для ручного завершения очереди."""
    db = async_session
    chat_id = 500
    mid = 50
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    chat = await queries_db.ensure_chat(db, chat_id, "VIP Чат")
    chat.subscription_tier = "supervip"
    chat.subscription_ends_at = now + timedelta(days=30)

    subj = await queries_db.get_or_create_subject(db, chat_id, "История")
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=mid,
        subject_id=subj.id,
        lesson_date=now + timedelta(hours=2),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value,
        participants=[201, 202],
    )
    await queries_db.ensure_user(db, 201, "student1", "Студент 1")
    await queries_db.ensure_user(db, 202, "student2", "Студент 2")
    await db.flush()

    reply_msg = _create_message("Очередь", chat_id=chat_id, message_id=mid)
    cmd_msg = _create_message(
        "/last @student2", chat_id=chat_id, user_id=201, reply_to=reply_msg
    )

    with patch("src.handlers.queue_common.safe_edit_text", AsyncMock()):
        await vip.cmd_last_formed(cmd_msg, db, bot)

    cmd_msg.answer.assert_awaited_once()
    assert "Готово" in cmd_msg.answer.await_args.args[0]

    updated_q = await queries_db.get_queue_by_chat_message(db, chat_id, mid)
    assert updated_q is not None
    assert updated_q.status == qc.QueueStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_cmd_shuffle_formed(async_session: AsyncSession) -> None:
    """Проверка перемешивания очереди через /shuffle."""
    db = async_session
    chat_id = 550
    mid = 60
    bot = MagicMock(spec=Bot)
    now = datetime.now(UTC)

    chat = await queries_db.ensure_chat(db, chat_id, "VIP Чат")
    chat.subscription_tier = "supervip"
    chat.subscription_ends_at = now + timedelta(days=30)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Экономика")
    participants = list(range(1001, 1015))
    await queries_db.add_queue_row(
        db,
        chat_id=chat_id,
        message_id=mid,
        subject_id=subj.id,
        lesson_date=now + timedelta(hours=3),
        close_at=None,
        status=qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value,
        participants=participants,
    )
    await db.flush()

    reply_msg = _create_message(
        "Список очереди", chat_id=chat_id, message_id=mid
    )
    cmd_msg = _create_message("/shuffle", chat_id=chat_id, reply_to=reply_msg)

    with patch("src.handlers.queue_common.refresh_queue_message", AsyncMock()):
        await vip.cmd_shuffle_formed(cmd_msg, db, bot)

    cmd_msg.answer.assert_awaited_once()
    assert "Порядок перемешан" in cmd_msg.answer.await_args.args[0]
