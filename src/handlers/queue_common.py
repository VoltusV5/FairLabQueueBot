"""Общие константы, клавиатуры и финализация очереди (для queue + scheduler)."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.db import queries as Q
from src.db.init_db import Queue
from src.services import queue_manager
from src.state import pending_confirmations

logger = logging.getLogger(__name__)


class QueueStatus(str, Enum):
    WAITING_FOR_PARTICIPANTS = "waiting_for_participants"
    WAITING_FOR_LAST_PARTICIPANT = "waiting_for_last_participant"
    COMPLETED = "completed"


def split_cb(data: str) -> list[str]:
    return data.split("|")


def kb_recruit(chat_id: int, msg_id: int) -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, я последний сдавший",
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Я последний сдавший",
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


_UTC = ZoneInfo("UTC")
_MSK = ZoneInfo("Europe/Moscow")


def _to_msk(d: datetime) -> datetime:
    """Наивные datetime в БД считаем UTC (как datetime.utcnow()), вывод — Москва.
    Если в БД уже лежит время в МСК, оно будет сдвинуто еще раз, поэтому 
    важно везде использовать UTC для хранения и расчетов."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=_UTC)
    return d.astimezone(_MSK)


def _from_msk_to_utc(d: datetime) -> datetime:
    """Конвертирует время из МСК (ввод пользователя) в UTC для хранения в БД."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=_MSK)
    return d.astimezone(_UTC)


def fmt_dt(d: datetime) -> tuple[str, str]:
    m = _to_msk(d)
    return m.strftime("%d.%m.%Y"), m.strftime("%H:%M")


def format_dt_msk_compact(d: datetime) -> str:
    """Одна строка дата+время для ответов бота (МСК)."""
    m = _to_msk(d)
    return m.strftime("%d.%m.%Y %H:%M")


def escape_html_text(s: str) -> str:
    """Безопасная подстановка пользовательских строк в HTML-сообщения."""
    return html.escape(s or "", quote=False)


def header_waiting(
    subject: str,
    lesson: datetime,
    close_at: datetime | None,
    *,
    implicit_lesson: bool = False,
) -> str:
    ds, ts = fmt_dt(lesson)
    subject = escape_html_text(subject)
    if implicit_lesson:
        auto = "нет"
    elif close_at:
        cds, cts = fmt_dt(close_at)
        auto = f"{cds} {cts}"
    else:
        auto = "нет"
    return (
        f"📘 <b>Запись на {subject}</b>\n"
        f"📅 {ds}\n"
        f"⏰ {ts}\n"
        f"⏱️ Автозакрытие записи: {auto}"
    )


def subject_from_formed(text: str) -> str:
    for line in (text or "").split("\n"):
        if "Список на" in line:
            return line.split("на", 1)[1].strip()
    return ""


def refused_slots_for_formed(extra: dict | None) -> set[int]:
    """Индексы слотов в formed_order с пометкой отказа (последняя запись tg_id в старом формате)."""
    ex = extra or {}
    raw = ex.get("refused_slot_indices")
    if raw is not None:
        return {int(x) for x in raw}
    order = list(ex.get("formed_order") or [])
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
    key = (chat_id, message_id)

    async def _job() -> None:
        await asyncio.sleep(30)
        if pending_confirmations.get(key, {}).get("kind") != kind:
            return
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=original_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        pending_confirmations.pop(key, None)

    t = asyncio.create_task(_job())
    pending_confirmations[key] = {"task": t, "kind": kind}


def cancel_pending(chat_id: int, message_id: int) -> None:
    key = (chat_id, message_id)
    p = pending_confirmations.pop(key, None)
    if p and "task" in p:
        p["task"].cancel()


async def finalize_queue_core(
    bot: Bot, db: Session, q: Queue, message: Message | None = None
) -> None:
    subj_row = Q.get_subject_by_id(db, q.subject_id)
    if not subj_row:
        return
    mid = message.message_id if message else q.message_id
    Q.delete_swaps_for_queue(db, mid)
    subj_name = escape_html_text(subj_row.subject_name)
    parts = list(q.participants or [])
    ex = q.extra or {}
    refused_tg = set(ex.get("refused_ids", []))
    ordered = queue_manager.order_tg_ids(db, parts, q.subject_id, q.chat_id)
    queue_manager.append_formation_history(db, ordered, q.subject_id)
    q.participants = ordered
    flag_modified(q, "participants")
    q.status = QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value
    now = datetime.utcnow()
    refused_slots: set[int] = set()
    for t in refused_tg:
        idxs = [i for i, x in enumerate(ordered) if x == t]
        if idxs:
            refused_slots.add(idxs[-1])
    Q.merge_extra(
        db,
        q,
        {
            "formed_order": ordered,
            "voting_started_at": now.isoformat(),
            "reminder_5h_sent": False,
            "deadline_24h_applied": False,
            "refused_slot_indices": sorted(refused_slots),
            "refused_ids": [],
        },
    )
    ds, ts = fmt_dt(q.lesson_date)
    body = queue_manager.format_queue_lines(db, ordered, refused_slots)
    head = f"<b>Список на {subj_name}</b>\n📅 {ds}\n⏰ {ts}\n"
    text = head + "\n" + body
    cid = message.chat.id if message else q.chat_id
    kb = kb_after_formed(cid, mid)
    if message:
        await message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=cid,
                message_id=mid,
                reply_markup=kb,
            )
        except Exception:
            pass
    else:
        try:
            await bot.edit_message_text(
                chat_id=cid,
                message_id=mid,
                text=text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning("finalize_queue_core edit_message_text: %s", e)
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=cid,
                message_id=mid,
                reply_markup=kb,
            )
        except Exception:
            pass


async def refresh_queue_message(
    bot: Bot, db: Session, q: Queue, message: Message | None = None
) -> None:
    subj_row = Q.get_subject_by_id(db, q.subject_id)
    if not subj_row:
        return
    ex = q.extra or {}
    ordered = ex.get("formed_order") or q.participants or []
    refused_slots = refused_slots_for_formed(ex)
    ds, ts = fmt_dt(q.lesson_date)
    body = queue_manager.format_queue_lines(db, list(ordered), refused_slots)
    subj_name = escape_html_text(subj_row.subject_name)
    head = f"<b>Список на {subj_name}</b>\n📅 {ds}\n⏰ {ts}\n"
    cid = message.chat.id if message else q.chat_id
    mid = message.message_id if message else q.message_id
    kb = kb_after_formed(cid, mid)
    full = head + "\n" + body
    try:
        if message:
            await message.edit_text(full, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await bot.edit_message_text(
                chat_id=cid,
                message_id=mid,
                text=full,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.warning("refresh_queue_message edit: %s", e)
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=cid,
            message_id=mid,
            reply_markup=kb,
        )
    except Exception:
        pass


async def finalize_queue_from_scheduler(bot: Bot, db: Session, q: Queue) -> None:
    if q.status != QueueStatus.WAITING_FOR_PARTICIPANTS.value:
        return
    await finalize_queue_core(bot, db, q, None)
