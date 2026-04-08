"""Фоновые задачи: автозакрытие, напоминания, дедлайны по «последнему сдавшему»."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy.orm.attributes import flag_modified

from ..db.db import get_db
from ..db import queries as Q
from ..db.init_db import Chat, Queue
from ..handlers import queue_common as qc_mod
from ..handlers.queue_common import format_dt_msk_compact
from ..services.subscription import current_access_deadline

logger = logging.getLogger(__name__)


async def run_periodic(bot: Bot) -> None:
    while True:
        try:
            await _tick(bot)
        except Exception as e:
            logger.exception("scheduler tick: %s", e)
        await asyncio.sleep(60)


async def _tick(bot: Bot) -> None:
    now = datetime.utcnow()
    with get_db() as db:
        await _subscription_reminders(bot, db, now)

        for q in Q.list_queues_recruiting(db):
            close_at = q.close_at
            if close_at and close_at <= now:
                # Проверяем, не была ли очередь уже закрыта вручную или удалена
                # Хотя list_queues_recruiting фильтрует по статусу, 
                # на всякий случай проверяем актуальность.
                await _autoclose_queue(bot, db, q)

        for q in Q.list_queues_waiting_last(db):
            ex = q.extra or {}
            voting_started = ex.get("voting_started_at")
            if not voting_started:
                continue
            if isinstance(voting_started, str):
                voting_started = datetime.fromisoformat(voting_started)
            if not isinstance(voting_started, datetime):
                continue

            if now >= voting_started + timedelta(hours=5) and not ex.get(
                "reminder_5h_sent"
            ):
                ex["reminder_5h_sent"] = True
                Q.merge_extra(db, q, ex)
                text = (
                    "⏰ Прошло 5 часов с начала голосования «кто последний». "
                    "Пожалуйста, отметьтесь кнопкой «Я последний сдавший», если вы сдавали последним."
                )
                try:
                    await bot.send_message(q.chat_id, text)
                except Exception as e:
                    logger.warning("reminder 5h: %s", e)

            if now >= voting_started + timedelta(hours=24) and not ex.get(
                "deadline_24h_applied"
            ):
                ex["deadline_24h_applied"] = True
                Q.merge_extra(db, q, ex)
                ordered = ex.get("formed_order") or q.participants or []
                tg_ids = [
                    x for x in dict.fromkeys(ordered) if isinstance(x, int)
                ]
                if tg_ids:
                    Q.increment_missed_for_tg_ids(db, tg_ids, q.subject_id)
                try:
                    await bot.send_message(
                        q.chat_id,
                        "⏰ Прошло 24 часа без отметки последнего сдавшего. "
                        "Всем участникам очереди добавлена +1 к попытке сдачи.",
                    )
                except Exception as e:
                    logger.warning("deadline 24h: %s", e)


async def _autoclose_queue(bot: Bot, db, q: Queue) -> None:
    await qc_mod.finalize_queue_from_scheduler(bot, db, q)


async def _subscription_reminders(bot: Bot, db, now: datetime) -> None:
    """Напоминания за 3 дня и за 1 сутки до окончания trial/подписки."""
    for chat in Q.list_all_chats(db):
        await _maybe_send_subscription_reminder(bot, db, chat, now)


async def _maybe_send_subscription_reminder(
    bot: Bot, db, chat: Chat, now: datetime
) -> None:
    deadline = current_access_deadline(chat, now)
    if deadline is None:
        return
    remaining = deadline - now
    if remaining.total_seconds() <= 0:
        return

    dl_key = deadline.isoformat()
    state = dict(chat.subscription_reminder_state or {})
    if state.get("deadline_iso") != dl_key:
        state = {"deadline_iso": dl_key, "d3": False, "d1": False}
        chat.subscription_reminder_state = state
        flag_modified(chat, "subscription_reminder_state")
        db.commit()
        state = dict(chat.subscription_reminder_state or {})

    send_3d = not state.get("d3") and timedelta(days=2) < remaining <= timedelta(
        days=3
    )
    send_1d = not state.get("d1") and timedelta(0) < remaining <= timedelta(days=1)

    if not send_3d and not send_1d:
        return

    if send_3d:
        text = (
            f"⏳ До окончания доступа этого чата (пробный период или подписка) "
            f"осталось 2–3 дня. Окончание: {format_dt_msk_compact(deadline)} (МСК).\n"
            f"Продлить: /pay"
        )
        kind = "d3"
    else:
        text = (
            f"⚠️ Заканчивается доступ чата: {format_dt_msk_compact(deadline)} (МСК) "
            f"(осталось менее суток).\n"
            f"Продлить: /pay"
        )
        kind = "d1"

    try:
        await bot.send_message(chat.chat_id, text)
    except Exception as e:
        logger.warning("subscription reminder chat=%s: %s", chat.chat_id, e)
        return

    state[kind] = True
    chat.subscription_reminder_state = state
    flag_modified(chat, "subscription_reminder_state")
    db.commit()
