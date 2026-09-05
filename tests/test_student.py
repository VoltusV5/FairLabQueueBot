"""Тесты для обработчиков студенческих команд (src/handlers/student.py)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.db.init_db import QueueStatus, SubmissionAttempt
from src.handlers import student


def _create_message(
    text: str,
    chat_id: int = 100,
    chat_type: ChatType = ChatType.GROUP,
    message_id: int = 1,
    user_id: int = 1001,
    username: str = "testuser",
    reply_to: Message | None = None,
) -> Message:
    """Создает мок объекта aiogram Message."""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = message_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.chat.title = "Тестовый чат"
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.first_name = "Test"
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
    username: str = "testuser",
    text: str = "👥 Кто сейчас на паре?\n\nПока никого.",
) -> CallbackQuery:
    """Создает мок объекта aiogram CallbackQuery."""
    cb = MagicMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = MagicMock(spec=User)
    cb.from_user.id = user_id
    cb.from_user.username = username
    cb.from_user.first_name = "Test"
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
async def test_cmd_start_private_and_group(
    async_session: AsyncSession,
) -> None:
    """Проверка команды /start в личном чате и группе."""
    db = async_session
    # Личный чат
    msg_priv = _create_message("/start", chat_id=1, chat_type=ChatType.PRIVATE)
    await student.cmd_start(msg_priv, db)
    msg_priv.answer.assert_awaited_once()
    assert msg_priv.answer.await_args.kwargs.get("reply_markup") is not None
    assert (
        "ID этого чата: <code>1</code>" in msg_priv.answer.await_args.args[0]
    )

    # Групповой чат
    msg_group = _create_message("/start", chat_id=10, chat_type=ChatType.GROUP)
    await student.cmd_start(msg_group, db)
    msg_group.answer.assert_awaited_once()
    assert (
        "ID этого чата: <code>10</code>" in msg_group.answer.await_args.args[0]
    )


@pytest.mark.asyncio
async def test_cmd_help_and_menu_attendance(
    async_session: AsyncSession,
) -> None:
    """Проверка справки /help и подсказки кнопки посещаемости."""
    db = async_session
    msg_help = _create_message("/help", chat_id=1, chat_type=ChatType.PRIVATE)
    await student.cmd_help(msg_help, db)
    msg_help.answer.assert_awaited_once()
    kb = msg_help.answer.await_args.kwargs.get("reply_markup")
    assert kb is not None
    assert len(kb.inline_keyboard[0]) == 2

    # Клик по кнопке Base
    cb_base = _create_callback(
        "help_tier|base", chat_id=1, message_id=10, user_id=1001
    )
    await student.cb_help_tier(cb_base)
    cb_base.message.edit_text.assert_awaited_once()
    assert "Base подписка" in cb_base.message.edit_text.await_args.args[0]
    cb_base.answer.assert_awaited_once()

    # Клик по кнопке SuperVIP
    cb_vip = _create_callback(
        "help_tier|supervip", chat_id=1, message_id=10, user_id=1001
    )
    await student.cb_help_tier(cb_vip)
    cb_vip.message.edit_text.assert_awaited_once()
    assert "SuperVIP подписка" in cb_vip.message.edit_text.await_args.args[0]
    cb_vip.answer.assert_awaited_once()

    msg_att = _create_message(
        student.MENU_ATTENDANCE, chat_id=1, chat_type=ChatType.PRIVATE
    )
    await student.menu_attendance_hint(msg_att, db)
    msg_att.answer.assert_awaited_once()
    assert "В групповом чате" in msg_att.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_subjects(
    async_session: AsyncSession,
) -> None:
    """Проверка просмотра списка предметов чата."""
    db = async_session
    chat_id = 150
    await queries_db.get_or_create_subject(db, chat_id, "Математика")
    await queries_db.get_or_create_subject(db, chat_id, "Физика")
    await db.flush()

    # В группе
    msg_group = _create_message(
        "/subjects", chat_id=chat_id, chat_type=ChatType.GROUP
    )
    await student.cmd_subjects(msg_group, db)
    msg_group.answer.assert_awaited_once()
    ans = msg_group.answer.await_args.args[0]
    assert "Математика" in ans and "Физика" in ans

    # В личке без аргументов
    msg_priv_no_arg = _create_message(
        "/subjects", chat_id=1, chat_type=ChatType.PRIVATE
    )
    await student.cmd_subjects(msg_priv_no_arg, db)
    assert (
        "В личке бота используйте" in msg_priv_no_arg.answer.await_args.args[0]
    )

    # В личке с валидным ID чата
    msg_priv_ok = _create_message(
        f"/subjects {chat_id}", chat_id=1, chat_type=ChatType.PRIVATE
    )
    await student.cmd_subjects(msg_priv_ok, db)
    ans_priv = msg_priv_ok.answer.await_args.args[0]
    assert "Математика" in ans_priv and "Физика" in ans_priv


@pytest.mark.asyncio
async def test_cmd_stat(
    async_session: AsyncSession,
) -> None:
    """Проверка выгрузки статистики /stat."""
    db = async_session
    bot = AsyncMock()

    # В группе — перенаправляет в личку
    msg_group = _create_message("/stat", chat_id=200, chat_type=ChatType.GROUP)
    await student.cmd_stat(msg_group, bot, db)
    msg_group.answer.assert_awaited_once()
    assert "в личных сообщениях" in msg_group.answer.await_args.args[0]

    # В личке
    msg_priv = _create_message(
        "/stat", chat_id=1, chat_type=ChatType.PRIVATE, user_id=123
    )
    await queries_db.ensure_user(db, 123, "testuser", "Test User")
    await student.cmd_stat(msg_priv, bot, db)
    bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_cmd_who_and_cb_presence(
    async_session: AsyncSession,
) -> None:
    """Проверка команды /who и нажатия кнопок отметки присутствия."""
    db = async_session
    bot = AsyncMock()
    chat_id = 300

    # /who в личке блокируется
    msg_priv = _create_message("/who", chat_id=1, chat_type=ChatType.PRIVATE)
    await student.cmd_who(msg_priv, bot, db)
    assert "только в группе" in msg_priv.answer.await_args.args[0]

    # /who в группе отправляет сообщение
    msg_group = _create_message(
        "/who Физика", chat_id=chat_id, chat_type=ChatType.GROUP
    )
    await student.cmd_who(msg_group, bot, db)
    msg_group.answer.assert_awaited_once()
    bot.edit_message_reply_markup.assert_awaited_once()

    # Кнопка 'На паре'
    cb_here = _create_callback(
        f"ph|{chat_id}|101|1",
        chat_id=chat_id,
        message_id=101,
        user_id=777,
        username="student7",
    )
    await student.cb_presence(cb_here, bot, db)
    cb_here.answer.assert_awaited_with("Ок")
    bot.edit_message_text.assert_awaited_once()

    # Кнопка 'Не на паре'
    cb_not_here = _create_callback(
        f"ph|{chat_id}|101|0",
        chat_id=chat_id,
        message_id=101,
        user_id=777,
        username="student7",
    )
    await student.cb_presence(cb_not_here, bot, db)
    cb_not_here.answer.assert_awaited_with("Снято")


@pytest.mark.asyncio
async def test_cmd_starosta_management(
    async_session: AsyncSession,
) -> None:
    """Проверка назначения и снятия старосты чата."""
    db = async_session
    chat_id = 400
    user_id = 888
    username = "newstarosta"
    await queries_db.ensure_user(db, user_id, username, "New Starosta")

    # Назначение старосты
    msg_set = _create_message(
        f"/set_starosta @{username}", chat_id=chat_id, chat_type=ChatType.GROUP
    )
    await student.cmd_set_starosta(msg_set, db)
    assert "назначен старостой" in msg_set.answer.await_args.args[0]

    # Повторное назначение
    await student.cmd_set_starosta(msg_set, db)
    assert "уже является старостой" in msg_set.answer.await_args.args[0]

    # Снятие старосты
    msg_rm = _create_message(
        f"/rm_starosta @{username}", chat_id=chat_id, chat_type=ChatType.GROUP
    )
    await student.cmd_rm_starosta(msg_rm, db)
    assert "удалён из списка старост" in msg_rm.answer.await_args.args[0]

    # Повторное снятие
    await student.cmd_rm_starosta(msg_rm, db)
    assert "не является старостой" in msg_rm.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_king_management(
    async_session: AsyncSession,
) -> None:
    """Проверка назначения и снятия короля предмета."""
    db = async_session
    chat_id = 500
    user_id = 999
    username = "kinguser"
    await queries_db.ensure_user(db, user_id, username, "King User")
    await queries_db.get_or_create_subject(db, chat_id, "Химия")

    # Назначение короля
    msg_king = _create_message(
        f"/king Химия @{username}", chat_id=chat_id, chat_type=ChatType.GROUP
    )
    await student.cmd_king(msg_king, db)
    assert "назначен" in msg_king.answer.await_args.args[0]

    # Повторное назначение
    await student.cmd_king(msg_king, db)
    assert "уже является королём" in msg_king.answer.await_args.args[0]

    # Снятие короля
    msg_unking = _create_message(
        f"/unking Химия @{username}", chat_id=chat_id, chat_type=ChatType.GROUP
    )
    await student.cmd_unking(msg_unking, db)
    assert "лишён статуса короля" in msg_unking.answer.await_args.args[0]

    # Повторное снятие
    await student.cmd_unking(msg_unking, db)
    assert "не является королём" in msg_unking.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_confirm_and_rm_queue(
    async_session: AsyncSession,
) -> None:
    """Проверка подтверждения помилования /confirm и удаления очереди /rm."""
    db = async_session
    bot = AsyncMock()
    chat_id = 600
    starosta_id = 1111
    student_id = 2222
    starosta_un = "superstarosta"
    student_un = "poorstudent"

    await queries_db.ensure_user(db, starosta_id, starosta_un, "Starosta")
    await queries_db.ensure_user(db, student_id, student_un, "Student")
    await queries_db.add_chat_admin(db, chat_id, starosta_id)

    subj = await queries_db.get_or_create_subject(db, chat_id, "Геометрия")

    # Создаем завершенную очередь
    reply_queue_msg = _create_message(
        f"Список на Геометрия\n1. @{student_un}",
        chat_id=chat_id,
        message_id=777,
    )
    await queries_db.add_queue_row(
        db,
        subject_id=subj.id,
        chat_id=chat_id,
        message_id=777,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        close_at=None,
        status=QueueStatus.COMPLETED,
        participants=[student_id],
        extra={"successful_slot_index": 0},
    )
    sub = SubmissionAttempt(
        tg_id=student_id,
        subject_id=subj.id,
        history_position=[{"pos": 1, "status": "submitted"}],
        missed_attempts_count=0,
    )
    db.add(sub)
    await db.flush()

    # Помилование через /confirm
    msg_confirm = _create_message(
        f"/confirm @{student_un}",
        chat_id=chat_id,
        user_id=starosta_id,
        reply_to=reply_queue_msg,
    )
    await student.cmd_confirm(msg_confirm, db)
    assert "помилован" in msg_confirm.answer.await_args.args[0] or (
        "отменён" in msg_confirm.answer.await_args.args[0]
    )

    # Удаление очереди через /rm
    msg_rm = _create_message(
        "/rm",
        chat_id=chat_id,
        user_id=starosta_id,
        reply_to=reply_queue_msg,
    )
    await student.cmd_rm_queue(msg_rm, bot, db)
    assert "удалена" in msg_rm.answer.await_args.args[0]
