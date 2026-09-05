"""Общие компоненты управления очередью.

Модуль инкапсулирует транспортную логику отображения очередей, безопасное
редактирование сообщений в Telegram, расчет временных зон (UTC <-> МСК),
сборку inline-клавиатур и операции завершения набора участников.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db import queries as queries_db
from src.db.init_db import Queue, QueueStatus
from src.services import queue_manager
from src.state import pending_confirmations

logger = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")
_MSK = ZoneInfo("Europe/Moscow")


def split_cb(data: str) -> list[str]:
    """Разбивает строку callback_data по разделителю '|'.

    Args:
        data: Исходная строка callback_data.

    Returns:
        list[str]: Список строковых фрагментов callback_data.
    """
    return data.split("|")


async def safe_edit_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: ParseMode | str | None = None,
) -> None:
    """Безопасно редактирует текст сообщения с обработкой Telegram API лимитов.

    Подавляет ошибку неизмененного сообщения ('message is not modified'),
    повторяет попытку при TelegramRetryAfter и логирует критические сбои.

    Args:
        bot: Экземпляр бота Telegram.
        chat_id: Идентификатор целевого чата.
        message_id: Идентификатор редактируемого сообщения.
        text: Новый текст сообщения.
        reply_markup: Опциональная inline-клавиатура.
        parse_mode: Режим парсинга разметки (HTML, Markdown и т.д.).
    """
    for _ in range(2):
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except TelegramRetryAfter as e:
            logger.warning(
                "Flood control (safe_edit_text). Повтор через %s с.",
                e.retry_after,
            )
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning("safe_edit_text bad request: %s", e)
            return
        except Exception as e:
            logger.warning("safe_edit_text exception: %s", e)
            return


def kb_recruit(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для этапа активного набора в очередь.

    Args:
        chat_id: Идентификатор чата.
        msg_id: Идентификатор сообщения очереди.

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками участия и управления.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Участвую",
                    callback_data=f"pa|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить участие",
                    callback_data=f"pr|{chat_id}|{msg_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Удалить запись",
                    callback_data=f"dq|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="Завершить досрочно",
                    callback_data=f"cq|{chat_id}|{msg_id}",
                ),
            ],
        ]
    )


def kb_confirm_close(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения досрочного закрытия набора.

    Args:
        chat_id: Идентификатор чата.
        msg_id: Идентификатор сообщения очереди.

    Returns:
        InlineKeyboardMarkup: Клавиатура подтверждения/отмены.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, завершить",
                    callback_data=f"cqy|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="Нет, отмена",
                    callback_data=f"cqn|{chat_id}|{msg_id}",
                ),
            ]
        ]
    )


def kb_confirm_del(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения удаления записи очереди.

    Args:
        chat_id: Идентификатор чата.
        msg_id: Идентификатор сообщения очереди.

    Returns:
        InlineKeyboardMarkup: Клавиатура подтверждения/отмены.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"dqy|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="Нет, отмена",
                    callback_data=f"dqn|{chat_id}|{msg_id}",
                ),
            ]
        ]
    )


def kb_confirm_refuse(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения отказа от сформированного места.

    Args:
        chat_id: Идентификатор чата.
        msg_id: Идентификатор сообщения очереди.

    Returns:
        InlineKeyboardMarkup: Клавиатура подтверждения/отмены.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отказаться",
                    callback_data=f"rfy|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="Нет, отмена",
                    callback_data=f"rfn|{chat_id}|{msg_id}",
                ),
            ]
        ]
    )


def kb_last_confirm(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения статуса последнего сдавшего студента.

    Args:
        chat_id: Идентификатор чата.
        msg_id: Идентификатор сообщения очереди.

    Returns:
        InlineKeyboardMarkup: Клавиатура подтверждения/отмены.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, я последний сдававший",
                    callback_data=f"ly|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="Нет, отмена",
                    callback_data=f"ln|{chat_id}|{msg_id}",
                ),
            ]
        ]
    )


def kb_after_formed(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру действий после формирования списка очереди.

    Args:
        chat_id: Идентификатор чата.
        msg_id: Идентификатор сообщения очереди.

    Returns:
        InlineKeyboardMarkup: Клавиатура действий в сформированной очереди.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Я последний сдававший",
                    callback_data=f"lp|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="В конец",
                    callback_data=f"ae|{chat_id}|{msg_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Отказаться",
                    callback_data=f"rf|{chat_id}|{msg_id}",
                ),
                InlineKeyboardButton(
                    text="Поменяться",
                    callback_data=f"sw|{chat_id}|{msg_id}",
                ),
            ],
        ]
    )


def _to_msk(d: datetime) -> datetime:
    """Конвертирует datetime в московское время (Europe/Moscow).

    Если объект времени наивный (без tzinfo), предполагается, что он в UTC.

    Args:
        d: Исходная дата и время.

    Returns:
        datetime: Дата и время в часовом поясе Europe/Moscow.
    """
    if d.tzinfo is None:
        d = d.replace(tzinfo=_UTC)
    return d.astimezone(_MSK)


def _from_msk_to_utc(d: datetime) -> datetime:
    """Конвертирует время из МСК (ввод пользователя) в UTC для сохранения в БД.

    Args:
        d: Исходная дата и время в часовом поясе МСК.

    Returns:
        datetime: Дата и время в часовом поясе UTC.
    """
    if d.tzinfo is None:
        d = d.replace(tzinfo=_MSK)
    return d.astimezone(_UTC)


def fmt_dt(d: datetime) -> tuple[str, str]:
    """Форматирует дату и время в строковые представления ДД.ММ.ГГГГ и ЧЧ:ММ.

    Args:
        d: Исходная дата и время.

    Returns:
        tuple[str, str]: Кортеж из двух строк: (дата, время) по МСК.
    """
    m = _to_msk(d)
    return m.strftime("%d.%m.%Y"), m.strftime("%H:%M")


def format_dt_msk_compact(d: datetime) -> str:
    """Форматирует дату и время в компактную строку для ответов бота (МСК).

    Args:
        d: Исходная дата и время.

    Returns:
        str: Строка в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'.
    """
    m = _to_msk(d)
    return m.strftime("%d.%m.%Y %H:%M")


def escape_html_text(s: str | None) -> str:
    """Безопасно экранирует пользовательские строки для HTML-сообщений.

    Args:
        s: Исходная строка или None.

    Returns:
        str: Экранированная строка без экранирования кавычек.
    """
    return html.escape(s or "", quote=False)


def header_waiting(
    subject: str,
    lesson: datetime,
    close_at: datetime | None,
    *,
    participants_count: int = 0,
    implicit_lesson: bool = False,
) -> str:
    """Формирует текстовую шапку сообщения для набора в очередь.

    Args:
        subject: Название предмета.
        lesson: Дата и время проведения занятия.
        close_at: Время автоматического закрытия записи или None.
        participants_count: Количество записавшихся участников.
        implicit_lesson: Флаг неявного расписания (без автозакрытия).

    Returns:
        str: Сформированный заголовок очереди в формате HTML.
    """
    ds, ts = fmt_dt(lesson)
    escaped_subject = escape_html_text(subject)
    if implicit_lesson or not close_at:
        auto = "нет"
    else:
        cds, cts = fmt_dt(close_at)
        auto = f"{cds} {cts}"
    return (
        f"📘 <b>Запись на {escaped_subject}</b>\n"
        f"📅 {ds}\n"
        f"⏰ {ts}\n"
        f"⏱️ Автозакрытие записи: {auto}\n"
        f"👥 Участвуют: {participants_count}"
    )


def subject_from_formed(text: str) -> str:
    """Извлекает название предмета из текста сформированной очереди.

    Очищает строку от HTML-тегов и пробелов для корректного сопоставления.

    Args:
        text: Текст сообщения с сформированной очередью.

    Returns:
        str: Извлеченное название предмета либо пустая строка.
    """
    for line in (text or "").split("\n"):
        clean_line = re.sub(r"<[^>]+>", "", line).strip()
        if "Список на" in clean_line:
            return clean_line.split("на", 1)[1].strip()
    return ""


def refused_slots_for_formed(q: Queue) -> set[int]:
    """Возвращает набор индексов слотов, от которых участники отказались.

    Сначала проверяет поле extra['refused_slot_indices'], при его отсутствии
    вычисляет последние позиции участников из extra['refused_ids'].

    Args:
        q: Объект очереди из базы данных.

    Returns:
        set[int]: Множество номеров (индексов с 0) слотов с отказом.
    """
    ex = q.extra or {}
    raw = ex.get("refused_slot_indices")
    if raw is not None:
        return {int(x) for x in raw}
    order = list(q.participants or [])
    refused_tg = set(ex.get("refused_ids", []) or [])
    out: set[int] = set()
    for t in refused_tg:
        ids = [i for i, x in enumerate(order) if x == t]
        if ids:
            out.add(ids[-1])
    return out


async def schedule_confirm_reset(
    bot: Bot,
    chat_id: int,
    message_id: int,
    original_text: str,
    reply_markup: InlineKeyboardMarkup,
    kind: str,
) -> None:
    """Планирует сброс окна подтверждения к исходному виду через 30 секунд.

    Args:
        bot: Экземпляр бота Telegram.
        chat_id: Идентификатор чата.
        message_id: Идентификатор сообщения.
        original_text: Исходный текст сообщения для восстановления.
        reply_markup: Исходная inline-клавиатура для восстановления.
        kind: Тип запрашиваемого подтверждения (например, 'del', 'close').
    """
    key = (chat_id, message_id)
    cancel_pending(chat_id, message_id)

    async def _job() -> None:
        try:
            await asyncio.sleep(30)
            entry = pending_confirmations.get(key)
            if not entry or entry.get("kind") != kind:
                return
            await safe_edit_text(
                bot,
                chat_id,
                message_id,
                original_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        except asyncio.CancelledError:
            return
        finally:
            if pending_confirmations.get(key, {}).get("kind") == kind:
                pending_confirmations.pop(key, None)

    task = asyncio.create_task(_job())
    pending_confirmations[key] = {"task": task, "kind": kind}


def cancel_pending(chat_id: int, message_id: int) -> None:
    """Отменяет отложенную задачу сброса подтверждения для сообщения.

    Args:
        chat_id: Идентификатор чата.
        message_id: Идентификатор сообщения.
    """
    key = (chat_id, message_id)
    pending = pending_confirmations.pop(key, None)
    if pending and "task" in pending:
        task = pending["task"]
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()


async def finalize_queue_core(
    bot: Bot,
    db: AsyncSession,
    q: Queue,
    message: Message | None = None,
) -> None:
    """Финализирует набор в очередь и переводит ее в статус ожидания сдачи.

    Выполняет справедливое ранжирование участников через queue_manager,
    фиксирует историю, обновляет статус и перерисовывает сообщение очереди.

    Args:
        bot: Экземпляр бота Telegram.
        db: Асинхронная сессия базы данных.
        q: Объект очереди.
        message: Опциональное сообщение aiogram (если вызвано из хэндлера).
    """
    subj_row = await queries_db.get_subject_by_id(db, q.subject_id)
    if not subj_row:
        logger.warning(
            "Не найден предмет subject_id=%s для очереди id=%s",
            q.subject_id,
            q.id,
        )
        return

    mid = message.message_id if message else q.message_id
    await queries_db.delete_swaps_for_queue(db, mid)

    subj_name = escape_html_text(subj_row.subject_name)
    parts = list(q.participants or [])
    ex = q.extra or {}
    refused_tg = set(ex.get("refused_ids", []))

    ordered = await queue_manager.order_tg_ids(
        db, parts, q.subject_id, q.chat_id
    )
    await queue_manager.append_formation_history(db, ordered, q.subject_id)

    q.participants = ordered
    flag_modified(q, "participants")
    q.status = QueueStatus.WAITING_FOR_LAST_PARTICIPANT

    now = datetime.now(_UTC)
    lesson_dt = q.lesson_date
    if lesson_dt is not None and lesson_dt.tzinfo is None:
        lesson_dt = lesson_dt.replace(tzinfo=_UTC)

    voting_started = lesson_dt if lesson_dt and lesson_dt > now else now

    refused_slots: set[int] = set()
    for t in refused_tg:
        idxs = [i for i, x in enumerate(ordered) if x == t]
        if idxs:
            refused_slots.add(idxs[-1])

    queries_db.merge_extra(
        q,
        {
            "voting_started_at": voting_started.isoformat(),
            "reminder_5h_sent": False,
            "deadline_24h_applied": False,
            "refused_slot_indices": sorted(refused_slots),
            "refused_ids": [],
        },
    )

    ds, ts = fmt_dt(q.lesson_date)
    kings = subj_row.kings or []
    body = await queue_manager.format_queue_lines(
        db,
        ordered,
        refused_slots,
        kings,
        temp_names=ex.get("temp_names"),
    )
    head = f"<b>Список на {subj_name}</b>\n📅 {ds}\n⏰ {ts}\n"
    full_text = head + "\n" + body
    cid = message.chat.id if message else q.chat_id
    kb = kb_after_formed(cid, mid)

    target_bot = message.bot if message and message.bot is not None else bot
    await safe_edit_text(
        target_bot,
        cid,
        mid,
        full_text,
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


async def refresh_queue_message(
    bot: Bot,
    db: AsyncSession,
    q: Queue,
    message: Message | None = None,
) -> None:
    """Обновляет сформированное сообщение очереди в Telegram.

    Синхронизирует отображаемый текст с текущим состоянием участников в БД.

    Args:
        bot: Экземпляр бота Telegram.
        db: Асинхронная сессия базы данных.
        q: Объект очереди.
        message: Опциональное сообщение aiogram.
    """
    subj_row = await queries_db.get_subject_by_id(db, q.subject_id)
    if not subj_row:
        return

    ex = q.extra or {}
    ordered = q.participants or []
    refused_slots = refused_slots_for_formed(q)
    ds, ts = fmt_dt(q.lesson_date)
    kings = subj_row.kings or []
    body = await queue_manager.format_queue_lines(
        db,
        list(ordered),
        refused_slots,
        kings,
        temp_names=ex.get("temp_names"),
    )
    subj_name = escape_html_text(subj_row.subject_name)
    head = f"<b>Список на {subj_name}</b>\n📅 {ds}\n⏰ {ts}\n"
    cid = message.chat.id if message else q.chat_id
    mid = message.message_id if message else q.message_id
    kb = kb_after_formed(cid, mid)
    full_text = head + "\n" + body

    target_bot = message.bot if message and message.bot is not None else bot
    await safe_edit_text(
        target_bot,
        cid,
        mid,
        full_text,
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


async def finalize_queue_from_scheduler(
    bot: Bot, db: AsyncSession, q: Queue
) -> None:
    """Финализирует очередь по срабатыванию планировщика автозакрытия.

    Args:
        bot: Экземпляр бота Telegram.
        db: Асинхронная сессия базы данных.
        q: Объект очереди.
    """
    if q.status != QueueStatus.WAITING_FOR_PARTICIPANTS:
        return
    await finalize_queue_core(bot, db, q, None)


__all__ = [
    "cancel_pending",
    "escape_html_text",
    "finalize_queue_core",
    "finalize_queue_from_scheduler",
    "fmt_dt",
    "format_dt_msk_compact",
    "header_waiting",
    "kb_after_formed",
    "kb_confirm_close",
    "kb_confirm_del",
    "kb_confirm_refuse",
    "kb_last_confirm",
    "kb_recruit",
    "refused_slots_for_formed",
    "refresh_queue_message",
    "safe_edit_text",
    "schedule_confirm_reset",
    "split_cb",
    "subject_from_formed",
    "_from_msk_to_utc",
    "_to_msk",
]
