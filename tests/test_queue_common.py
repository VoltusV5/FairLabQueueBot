"""Тесты для модуля queue_common и связанной логики queue_manager."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from src.db.init_db import Queue, QueueStatus, Subject
from src.handlers import queue_common as qc
from src.services import queue_manager
from src.state import pending_confirmations


def test_split_cb() -> None:
    """Проверка разбиения строки callback_data."""
    assert qc.split_cb("pa|123|456") == ["pa", "123", "456"]
    assert qc.split_cb("single") == ["single"]


def test_escape_html_text() -> None:
    """Проверка безопасного экранирования спецсимволов HTML."""
    assert qc.escape_html_text("A & B < C > D") == "A &amp; B &lt; C &gt; D"
    assert qc.escape_html_text("") == ""
    assert qc.escape_html_text(None) == ""


def test_subject_from_formed() -> None:
    """Проверка извлечения предмета из текста сформированной очереди."""
    raw_html = "<b>Список на Физика</b>\n📅 01.09.2026\n⏰ 10:00\n1. Иван\n"
    assert qc.subject_from_formed(raw_html) == "Физика"

    plain = "Список на Высшая математика\n📅 01.09.2026\n"
    assert qc.subject_from_formed(plain) == "Высшая математика"

    assert qc.subject_from_formed("Простой текст без очереди") == ""
    assert qc.subject_from_formed("") == ""


def test_refused_slots_for_formed() -> None:
    """Проверка определения индексов слотов с отказом."""
    q = Queue(
        subject_id=1,
        chat_id=100,
        message_id=200,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[101, 102, 103],
        extra={"refused_slot_indices": [1]},
    )
    assert qc.refused_slots_for_formed(q) == {1}

    # Fallback на refused_ids
    q2 = Queue(
        subject_id=1,
        chat_id=100,
        message_id=201,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[101, 102, 103],
        extra={"refused_ids": [103]},
    )
    assert qc.refused_slots_for_formed(q2) == {2}


def test_header_waiting() -> None:
    """Проверка форматирования шапки записи в очередь."""
    lesson_dt = datetime(2026, 9, 1, 7, 0, tzinfo=UTC)  # 10:00 MSK
    close_dt = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)  # 09:00 MSK

    res = qc.header_waiting(
        subject="Базы данных",
        lesson=lesson_dt,
        close_at=close_dt,
        participants_count=5,
        implicit_lesson=False,
    )
    assert "Базы данных" in res
    assert "01.09.2026" in res
    assert "10:00" in res
    assert "09:00" in res
    assert "Участвуют: 5" in res

    res_implicit = qc.header_waiting(
        subject="Базы данных",
        lesson=lesson_dt,
        close_at=close_dt,
        participants_count=3,
        implicit_lesson=True,
    )
    assert "Автозакрытие записи: нет" in res_implicit


def test_timezone_helpers() -> None:
    """Проверка конвертации временных зон (UTC <-> MSK)."""
    msk_tz = ZoneInfo("Europe/Moscow")
    utc_tz = ZoneInfo("UTC")

    naive_utc = datetime(2026, 9, 1, 10, 0)
    msk = qc._to_msk(naive_utc)
    assert msk.tzinfo == msk_tz
    assert msk.hour == 13

    naive_msk = datetime(2026, 9, 1, 13, 0)
    utc = qc._from_msk_to_utc(naive_msk)
    assert utc.tzinfo == utc_tz
    assert utc.hour == 10

    d, t = qc.fmt_dt(naive_utc)
    assert d == "01.09.2026"
    assert t == "13:00"

    compact = qc.format_dt_msk_compact(naive_utc)
    assert compact == "01.09.2026 13:00"


def test_keyboards() -> None:
    """Проверка структуры inline-клавиатур."""
    chat_id, msg_id = 123, 456
    kb_rec = qc.kb_recruit(chat_id, msg_id)
    assert isinstance(kb_rec, InlineKeyboardMarkup)
    assert len(kb_rec.inline_keyboard) == 2

    assert "pa|123|456" in kb_rec.inline_keyboard[0][0].callback_data
    assert "pr|123|456" in kb_rec.inline_keyboard[0][1].callback_data

    kb_close = qc.kb_confirm_close(chat_id, msg_id)
    assert "cqy|123|456" in kb_close.inline_keyboard[0][0].callback_data

    kb_del = qc.kb_confirm_del(chat_id, msg_id)
    assert "dqy|123|456" in kb_del.inline_keyboard[0][0].callback_data

    kb_refuse = qc.kb_confirm_refuse(chat_id, msg_id)
    assert "rfy|123|456" in kb_refuse.inline_keyboard[0][0].callback_data

    kb_last = qc.kb_last_confirm(chat_id, msg_id)
    assert "ly|123|456" in kb_last.inline_keyboard[0][0].callback_data

    kb_formed = qc.kb_after_formed(chat_id, msg_id)
    assert "lp|123|456" in kb_formed.inline_keyboard[0][0].callback_data


@pytest.mark.asyncio
async def test_safe_edit_text_success() -> None:
    """Проверка успешного редактирования сообщения safe_edit_text."""
    bot = AsyncMock()
    await qc.safe_edit_text(bot, 100, 200, "Новый текст")
    bot.edit_message_text.assert_awaited_once_with(
        chat_id=100,
        message_id=200,
        text="Новый текст",
        reply_markup=None,
        parse_mode=None,
    )


@pytest.mark.asyncio
async def test_safe_edit_text_retry_after() -> None:
    """Проверка обработки flood limit (TelegramRetryAfter) в safe_edit_text."""
    bot = AsyncMock()
    retry_err = TelegramRetryAfter(
        method=MagicMock(),
        message="Flood",
        retry_after=0.01,
    )
    bot.edit_message_text.side_effect = [retry_err, MagicMock()]

    await qc.safe_edit_text(bot, 100, 200, "Текст")
    assert bot.edit_message_text.await_count == 2


@pytest.mark.asyncio
async def test_safe_edit_text_not_modified() -> None:
    """Проверка игнорирования ошибки 'message is not modified'."""
    bot = AsyncMock()
    bad_req = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: message is not modified: specified new content",
    )
    bot.edit_message_text.side_effect = bad_req

    # Не должно бросать исключение
    await qc.safe_edit_text(bot, 100, 200, "Текст")
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_confirm_reset_and_cancel() -> None:
    """Проверка планирования сброса подтверждения и его отмены."""
    bot = AsyncMock()
    key = (999, 888)
    pending_confirmations.clear()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await qc.schedule_confirm_reset(
            bot=bot,
            chat_id=999,
            message_id=888,
            original_text="Оригинальный текст",
            reply_markup=qc.kb_recruit(999, 888),
            kind="del",
        )
        assert key in pending_confirmations
        assert pending_confirmations[key]["kind"] == "del"

        # Отменяем
        qc.cancel_pending(999, 888)
        assert key not in pending_confirmations


@pytest.mark.asyncio
async def test_finalize_queue_core_async() -> None:
    """Проверка асинхронной финализации очереди."""
    bot = AsyncMock()
    db = AsyncMock()

    subject = Subject(id=1, chat_id=100, subject_name="Физика", kings=[])
    queue = Queue(
        id=10,
        subject_id=1,
        chat_id=100,
        message_id=555,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        status=QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants=[101, 102],
        extra={"refused_ids": [102]},
    )

    p_subj = patch("src.db.queries.get_subject_by_id", new_callable=AsyncMock)
    p_swaps = patch(
        "src.db.queries.delete_swaps_for_queue", new_callable=AsyncMock
    )
    p_merge = patch("src.db.queries.merge_extra")
    p_order = patch(
        "src.services.queue_manager.order_tg_ids", new_callable=AsyncMock
    )
    p_hist = patch(
        "src.services.queue_manager.append_formation_history",
        new_callable=AsyncMock,
    )
    p_format = patch(
        "src.services.queue_manager.format_queue_lines",
        new_callable=AsyncMock,
    )
    p_edit = patch(
        "src.handlers.queue_common.safe_edit_text", new_callable=AsyncMock
    )

    with (
        p_subj as mock_subj,
        p_swaps as mock_swaps,
        p_merge as mock_merge,
        p_order as mock_order,
        p_hist as mock_history,
        p_format as mock_format,
        p_edit as mock_edit,
    ):
        mock_subj.return_value = subject
        mock_order.return_value = [101, 102]
        mock_format.return_value = "1. Пользователь 1\n2. Пользователь 2"

        await qc.finalize_queue_core(bot, db, queue, None)

        mock_swaps.assert_awaited_once_with(db, 555)
        mock_order.assert_awaited_once_with(db, [101, 102], 1, 100)
        mock_history.assert_awaited_once_with(db, [101, 102], 1)
        mock_format.assert_awaited_once()
        mock_edit.assert_awaited_once()
        assert queue.status == QueueStatus.WAITING_FOR_LAST_PARTICIPANT
        mock_merge.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_queue_message() -> None:
    """Проверка обновления сообщения сформированной очереди."""
    bot = AsyncMock()
    db = AsyncMock()
    subject = Subject(id=1, chat_id=100, subject_name="Физика", kings=[])
    queue = Queue(
        id=10,
        subject_id=1,
        chat_id=100,
        message_id=555,
        lesson_date=datetime(2026, 9, 1, 10, 0),
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[101, 102],
        extra={"refused_slot_indices": [1]},
    )

    p_subj = patch("src.db.queries.get_subject_by_id", new_callable=AsyncMock)
    p_format = patch(
        "src.services.queue_manager.format_queue_lines",
        new_callable=AsyncMock,
    )
    p_edit = patch(
        "src.handlers.queue_common.safe_edit_text", new_callable=AsyncMock
    )

    with (
        p_subj as mock_subj,
        p_format as mock_format,
        p_edit as mock_edit,
    ):
        mock_subj.return_value = subject
        mock_format.return_value = "1. Пользователь 1\n2. Пользователь 2"

        await qc.refresh_queue_message(bot, db, queue, None)

        mock_format.assert_awaited_once()
        mock_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_queue_from_scheduler() -> None:
    """Проверка финализации очереди планировщиком."""
    bot = AsyncMock()
    db = AsyncMock()
    queue = Queue(
        id=10,
        subject_id=1,
        chat_id=100,
        message_id=555,
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
    )
    p_core = patch(
        "src.handlers.queue_common.finalize_queue_core",
        new_callable=AsyncMock,
    )
    with p_core as mock_core:
        # Не должна финализироваться, если статус не WAITING_FOR_PARTICIPANTS
        await qc.finalize_queue_from_scheduler(bot, db, queue)
        mock_core.assert_not_called()

        queue.status = QueueStatus.WAITING_FOR_PARTICIPANTS
        await qc.finalize_queue_from_scheduler(bot, db, queue)
        mock_core.assert_awaited_once_with(bot, db, queue, None)


@pytest.mark.asyncio
async def test_queue_manager_order_and_format() -> None:
    """Проверка сервисной логики queue_manager."""
    db = AsyncMock()
    sub_attempt = MagicMock()
    sub_attempt.history_position = [{"pos": 1, "status": "submitted"}]
    sub_attempt.missed_attempts_count = 0

    p_rows = patch(
        "src.db.queries.ensure_submission_rows", new_callable=AsyncMock
    )
    p_chat = patch("src.db.queries.get_chat", new_callable=AsyncMock)
    p_disp = patch(
        "src.db.queries.get_users_display_map", new_callable=AsyncMock
    )
    p_hist = patch(
        "src.db.queries.add_history_positions", new_callable=AsyncMock
    )

    with (
        p_rows as mock_rows,
        p_chat as mock_chat,
        p_disp as mock_disp,
        p_hist as mock_hist,
    ):
        mock_rows.return_value = {101: sub_attempt}
        mock_chat.return_value = None
        mock_disp.return_value = {101: "Alice", -1: "Bob_temp"}

        ordered = await queue_manager.order_tg_ids(db, [101, -1], subject_id=1)
        assert 101 in ordered
        assert -1 in ordered

        formatted = await queue_manager.format_queue_lines(
            db=db,
            ordered_tg_ids=[101, -1],
            refused_slot_indices={0},
            kings=[101],
            temp_names={"-1": "Bob_temp"},
        )
        assert "Alice 👑 (отказался от участия в очереди)" in formatted
        assert "Bob_temp" in formatted

        await queue_manager.append_formation_history(
            db, [101, -1], subject_id=1
        )
        mock_hist.assert_awaited_once_with(db, [101], 1)
