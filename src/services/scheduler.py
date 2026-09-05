"""Фоновые периодические задачи бота.

Модуль реализует регламентный опрос БД (раз в минуту) для:
    1. Активации отложенных тарифов подписки при наступлении даты;
    2. Отправки напоминаний об окончании подписки/пробного периода;
    3. Автоматического закрытия набора в очереди по дедлайну (close_at);
    4. Отправки напоминания о голосовании «кто последний» через 5 часов;
    5. Начисления штрафных попыток участникам через 24 часа без отметки.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db import queries as queries_db
from src.db.init_db import Chat, Queue
from src.handlers import queue_common as qc_mod
from src.handlers.queue_common import format_dt_msk_compact
from src.services.subscription import current_access_deadline

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Приводит объект даты и времени к часовому поясу UTC.

    Args:
        dt: Объект datetime или None.

    Returns:
        datetime | None: Объект с tzinfo=timezone.utc или None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def run_periodic(bot: Bot) -> None:
    """Запускает бесконечный цикл фоновых периодических проверок.

    Args:
        bot: Экземпляр Telegram-бота.
    """
    while True:
        try:
            await _tick(bot)
        except Exception as e:
            logger.exception("Ошибка итерации планировщика: %s", e)
        await asyncio.sleep(60)


async def _tick(bot: Bot) -> None:
    """Выполняет одну итерацию регламентных проверок и обновления статусов.

    Args:
        bot: Экземпляр Telegram-бота.
    """
    from src.db.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        now = datetime.now(UTC)
        db: AsyncSession = session
        await _apply_pending_tiers(bot, db, now)
        await _subscription_reminders(bot, db, now)

        for q in await queries_db.list_queues_recruiting(db):
            close_at_utc = _ensure_utc(q.close_at)
            if close_at_utc and close_at_utc <= now:
                await _autoclose_queue(bot, db, q)

        for q in await queries_db.list_queues_waiting_last(db):
            ex = dict(q.extra or {})
            raw_vs = ex.get("voting_started_at")
            if not raw_vs:
                continue
            voting_started: datetime | None
            if isinstance(raw_vs, str):
                try:
                    voting_started = datetime.fromisoformat(raw_vs)
                except ValueError:
                    continue
            elif isinstance(raw_vs, datetime):
                voting_started = raw_vs
            else:
                continue

            voting_started_utc = _ensure_utc(voting_started)
            if voting_started_utc is None:
                continue

            if now >= voting_started_utc + timedelta(hours=5) and not ex.get(
                "reminder_5h_sent"
            ):
                ex["reminder_5h_sent"] = True
                queries_db.merge_extra(q, ex)
                text = (
                    "⏰ Прошло 5 часов с начала голосования «кто последний». "
                    "Пожалуйста, отметьтесь кнопкой «Я последний "
                    "сдававший», если вы сдавали последним."
                )
                try:
                    await bot.send_message(q.chat_id, text)
                except Exception as e:
                    logger.warning("reminder 5h chat=%s: %s", q.chat_id, e)

            if now >= voting_started_utc + timedelta(hours=24) and not ex.get(
                "deadline_24h_applied"
            ):
                ex["deadline_24h_applied"] = True
                queries_db.merge_extra(q, ex)
                ordered = q.participants or []
                tg_ids = [
                    x for x in dict.fromkeys(ordered) if isinstance(x, int)
                ]
                if tg_ids:
                    await queries_db.increment_missed_for_tg_ids(
                        db, tg_ids, q.subject_id
                    )
                try:
                    await bot.send_message(
                        q.chat_id,
                        "⏰ Прошло 24 часа без отметки последнего сдавшего. "
                        "Всем участникам очереди добавлена +1 к попытке "
                        "сдачи.",
                    )
                except Exception as e:
                    logger.warning("deadline 24h chat=%s: %s", q.chat_id, e)

        await db.commit()


async def _autoclose_queue(bot: Bot, db: AsyncSession, q: Queue) -> None:
    """Финализирует очередь по истечении дедлайна набора участников.

    Args:
        bot: Экземпляр Telegram-бота.
        db: Асинхронная сессия БД.
        q: Объект очереди.
    """
    await qc_mod.finalize_queue_from_scheduler(bot, db, q)


async def _apply_pending_tiers(
    bot: Bot, db: AsyncSession, now: datetime
) -> None:
    """Применяет отложенные тарифы подписки, время которых наступило.

    Args:
        bot: Экземпляр Telegram-бота.
        db: Асинхронная сессия БД.
        now: Текущие дата и время в UTC.
    """
    for chat in await queries_db.list_all_chats(db):
        activates_at_utc = _ensure_utc(chat.pending_tier_activates_at)
        if chat.pending_tier and activates_at_utc and activates_at_utc <= now:
            old_tier = chat.subscription_tier
            target_tier = chat.pending_tier
            chat.subscription_tier = target_tier
            chat.pending_tier = None
            chat.pending_tier_activates_at = None

            db.add(chat)
            logger.info(
                "Chat %s upgraded from %s to pending_tier %s",
                chat.chat_id,
                old_tier,
                target_tier,
            )
            tier_name = str(target_tier).upper()
            try:
                await bot.send_message(
                    chat.chat_id,
                    f"🎉 Ваш отложенный тариф <b>{tier_name}</b> активирован!",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(
                    "Failed to notify chat %s about pending_tier: %s",
                    chat.chat_id,
                    e,
                )

    await db.commit()


async def _subscription_reminders(
    bot: Bot, db: AsyncSession, now: datetime
) -> None:
    """Отправляет напоминания за 3 дня и за 1 день до окончания подписки.

    Args:
        bot: Экземпляр Telegram-бота.
        db: Асинхронная сессия БД.
        now: Текущие дата и время в UTC.
    """
    for chat in await queries_db.list_all_chats(db):
        await _maybe_send_subscription_reminder(bot, db, chat, now)


async def _maybe_send_subscription_reminder(
    bot: Bot, db: AsyncSession, chat: Chat, now: datetime
) -> None:
    """Отправляет напоминание об истечении срока доступа чата.

    Args:
        bot: Экземпляр Telegram-бота.
        db: Асинхронная сессия БД.
        chat: Объект чата.
        now: Текущие дата и время в UTC.
    """
    raw_deadline = current_access_deadline(chat, now)
    deadline = _ensure_utc(raw_deadline)
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
        await db.commit()
        state = dict(chat.subscription_reminder_state or {})

    send_3d = not state.get("d3") and timedelta(
        days=2
    ) < remaining <= timedelta(days=3)
    send_1d = not state.get("d1") and timedelta(0) < remaining <= timedelta(
        days=1
    )

    if not send_3d and not send_1d:
        return

    dl_formatted = format_dt_msk_compact(deadline)
    if send_3d:
        text = (
            "⏳ До окончания доступа этого чата (пробный период "
            f"или подписка) осталось 2–3 дня. Окончание: {dl_formatted} "
            "(МСК).\nПродлить: /pay"
        )
        kind = "d3"
    else:
        text = (
            f"⚠️ Заканчивается доступ чата: {dl_formatted} (МСК) "
            "(осталось менее суток).\nПродлить: /pay"
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
    await db.commit()
