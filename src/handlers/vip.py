"""Команды SuperVIP и настройки чата.

Поддерживаемые команды:
    /changename - изменение отображаемого имени пользователя;
    /auto - переключение автозакрытия по дефолтным правилам;
    /closeafter - установка дедлайна через N минут/часов;
    /closebefore - установка дедлайна за N минут/часов до занятия;
    /closeat - установка фиксированного времени закрытия набора;
    /newautorule - настройка кастомных интервалов автозакрытия;
    /group - создание группы участников для совместной записи;
    /insert - вставка участника на определенную позицию;
    /last - ручное завершение очереди с отметкой последнего сдавшего;
    /shuffle - случайное перемешивание сформированной очереди.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db import queries as queries_db
from src.handlers import queue_common as qc
from src.services.subscription import effective_access, has_supervip
from src.utils.autoclose_rules import parse_newautorule_line
from src.utils.parse_queue import (
    parse_date_time_tokens,
    parse_duration_minutes,
    parse_subject_datetime_tokens,
)

logger = logging.getLogger(__name__)

router = Router()


def _actor_label(message: Message) -> str:
    """Возвращает читаемую метку пользователя, выполнившего действие.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        str: Юзернейм с @, имя пользователя или его строковый ID.
    """
    if not message.from_user:
        return "неизвестный пользователь"
    if message.from_user.username:
        return f"@{message.from_user.username}"
    return message.from_user.full_name or str(message.from_user.id)


def _first_mention_tg_id(message: Message) -> int | None:
    """Извлекает ID пользователя из первого текстового упоминания.

    Args:
        message: Входящее сообщение Telegram.

    Returns:
        int | None: Telegram ID пользователя или None при его отсутствии.
    """
    for e in message.entities or []:
        if e.type == MessageEntityType.TEXT_MENTION and e.user:
            return e.user.id
    return None


async def _resolve_target_tg_id(
    message: Message, db: AsyncSession, tokens: list[str]
) -> tuple[int | None, list[str]]:
    """Извлекает Telegram ID цели из упоминания, @username или числового ID.

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        tokens: Список строковых токенов аргументов команды.

    Returns:
        tuple[int | None, list[str]]: Найденный ID (или None) и оставшиеся
            токены.
    """
    if not tokens:
        return None, []
    uid = _first_mention_tg_id(message)
    if uid is not None:
        return uid, tokens[1:]
    first = tokens[0].strip()
    if first.isdigit():
        return int(first), tokens[1:]
    raw_username = first.lstrip("@")
    u = await queries_db.find_user_by_username(db, raw_username)
    if u:
        return u.tg_id, tokens[1:]
    return None, tokens


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Приводит объект даты и времени к часовому поясу UTC.

    Args:
        dt: Объект datetime или None.

    Returns:
        datetime | None: Объект datetime с tzinfo=timezone.utc или None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@router.message(Command("changename"))
async def cmd_changename(message: Message, db: AsyncSession) -> None:
    """Устанавливает отображаемое имя пользователя для очередей.

    Использование: /changename Иван Иванов

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
    """
    if not message.from_user or not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /changename Иван Иванов")
        return
    new_name = parts[1].strip()
    await queries_db.ensure_user(
        db,
        message.from_user.id,
        message.from_user.username,
        new_name,
    )
    await db.flush()
    await message.answer("Имя для отображения обновлено.")


@router.message(Command("auto"))
async def cmd_auto(message: Message, db: AsyncSession) -> None:
    """Переключает статус автозакрытия очередей по умолчанию в чате.

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
    """
    chat = await queries_db.ensure_chat(
        db, message.chat.id, message.chat.title
    )
    chat.autoclose_enabled = not chat.autoclose_enabled
    await db.flush()
    status_label = "включено." if chat.autoclose_enabled else "выключено."
    await message.answer(
        "Автозакрытие по правилам по умолчанию для новых очередей: "
        + status_label
    )


@router.message(Command("closeafter"))
async def cmd_closeafter(message: Message, db: AsyncSession, bot: Bot) -> None:
    """Устанавливает время закрытия набора через указанный интервал.

    Использование: ответом на сообщение с открытым набором: /closeafter 30м

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        bot: Экземпляр Telegram-бота.
    """
    if not message.reply_to_message or not message.text:
        await message.answer(
            "Ответьте этой командой на сообщение с активной очередью "
        )
        return
    tok = message.text.split()
    if len(tok) < 2:
        await message.answer("Пример: /closeafter 30м  или  /closeafter 2ч")
        return
    dur = parse_duration_minutes(tok[1])
    if dur is None:
        await message.answer(
            "Не удалось разобрать длительность. Используйте суффиксы 'м' "
            "или 'ч' (например, 30м, 2ч)."
        )
        return

    now_utc = datetime.now(UTC)
    until = now_utc + timedelta(minutes=dur)
    mid = message.reply_to_message.message_id
    cid = message.chat.id

    q = await queries_db.get_queue_by_chat_message(
        db, cid, mid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
        await message.answer("Очередь в статусе набора не найдена.")
        return

    q.close_at = until
    queries_db.merge_extra(
        q, {"manual_closeafter": True, "autoclose_disabled": False}
    )
    await db.flush()

    subj_row = await queries_db.get_subject_by_id(db, q.subject_id)
    if subj_row:
        ex = q.extra or {}
        head = qc.header_waiting(
            subj_row.subject_name,
            q.lesson_date,
            q.close_at,
            participants_count=len(q.participants or []),
            implicit_lesson=bool(ex.get("implicit_lesson", False)),
        )
        await qc.safe_edit_text(
            bot=bot,
            chat_id=cid,
            message_id=mid,
            text=head,
            reply_markup=qc.kb_recruit(cid, mid),
        )

    await message.answer(
        f"Набор закроется не раньше {qc.format_dt_msk_compact(until)} (МСК).\n"
        f"Изменил: {_actor_label(message)}"
    )


@router.message(Command("closebefore"))
async def cmd_closebefore(
    message: Message, db: AsyncSession, bot: Bot
) -> None:
    """Устанавливает дедлайн закрытия за указанное время до начала занятия.

    Использование: ответом на открытый набор: /closebefore 30м

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        bot: Экземпляр Telegram-бота.
    """
    if not message.reply_to_message or not message.text:
        await message.answer(
            "Ответьте этой командой на сообщение с активной очередью "
        )
        return
    tok = message.text.split()
    if len(tok) < 2:
        await message.answer("Пример: /closebefore 30м  или  /closebefore 2ч")
        return
    dur = parse_duration_minutes(tok[1])
    if dur is None:
        await message.answer(
            "Не удалось разобрать длительность. Используйте суффиксы 'м' "
            "или 'ч' (например, 30м, 2ч)."
        )
        return

    mid = message.reply_to_message.message_id
    cid = message.chat.id
    now_utc = datetime.now(UTC)

    q = await queries_db.get_queue_by_chat_message(
        db, cid, mid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
        await message.answer("Очередь в статусе набора не найдена.")
        return

    lesson_utc = _ensure_utc(q.lesson_date)
    if lesson_utc is None:
        await message.answer("У очереди не указана дата занятия.")
        return

    until = lesson_utc - timedelta(minutes=dur)
    if until <= now_utc:
        await message.answer(
            "Время закрытия в прошлом. "
            "Уменьшите интервал для /closebefore или используйте /closeafter."
        )
        return

    q.close_at = until
    queries_db.merge_extra(
        q, {"manual_closebefore": True, "autoclose_disabled": False}
    )
    await db.flush()

    subj_row = await queries_db.get_subject_by_id(db, q.subject_id)
    if subj_row:
        ex = q.extra or {}
        head = qc.header_waiting(
            subj_row.subject_name,
            q.lesson_date,
            q.close_at,
            participants_count=len(q.participants or []),
            implicit_lesson=bool(ex.get("implicit_lesson", False)),
        )
        await qc.safe_edit_text(
            bot=bot,
            chat_id=cid,
            message_id=mid,
            text=head,
            reply_markup=qc.kb_recruit(cid, mid),
        )

    await message.answer(
        f"Набор закроется не позже {qc.format_dt_msk_compact(until)} (МСК) — "
        f"за {tok[1]} до времени очереди.\n"
        f"Изменил: {_actor_label(message)}"
    )


@router.message(Command("closeat"))
async def cmd_closeat(message: Message, db: AsyncSession, bot: Bot) -> None:
    """Устанавливает точное время закрытия набора в очередь.

    Использование: ответом на открытый набор: /closeat 15.04 14:30 или 14:30

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        bot: Экземпляр Telegram-бота.
    """
    if not message.reply_to_message or not message.text:
        await message.answer(
            "Ответьте этой командой на сообщение с активной очередью "
        )
        return
    tok = message.text.split()[1:]
    if not tok:
        await message.answer("Пример: /closeat 15.04 14:30 или /closeat 14:30")
        return

    now_utc = datetime.now(UTC)
    now_msk = now_utc + timedelta(hours=3)

    fake_parts = ["dummy"] + tok
    try:
        _, d, t = parse_subject_datetime_tokens(fake_parts, now_msk)
        if d is None and t is None:
            await message.answer("Не удалось разобрать дату/время.")
            return
        until_msk = parse_date_time_tokens(d, t, now_msk)
        until_utc = _ensure_utc(until_msk - timedelta(hours=3))
    except Exception as e:
        await message.answer(f"Ошибка формата времени: {e}")
        return

    if until_utc is None or until_utc <= now_utc:
        await message.answer("Указанное время уже в прошлом.")
        return

    mid = message.reply_to_message.message_id
    cid = message.chat.id

    q = await queries_db.get_queue_by_chat_message(
        db, cid, mid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
        await message.answer("Очередь в статусе набора не найдена.")
        return

    q.close_at = until_utc
    queries_db.merge_extra(
        q, {"manual_closeafter": True, "autoclose_disabled": False}
    )
    await db.flush()

    subj_row = await queries_db.get_subject_by_id(db, q.subject_id)
    if subj_row:
        ex = q.extra or {}
        head = qc.header_waiting(
            subj_row.subject_name,
            q.lesson_date,
            q.close_at,
            participants_count=len(q.participants or []),
            implicit_lesson=bool(ex.get("implicit_lesson", False)),
        )
        await qc.safe_edit_text(
            bot=bot,
            chat_id=cid,
            message_id=mid,
            text=head,
            reply_markup=qc.kb_recruit(cid, mid),
        )

    await message.answer(
        f"Набор закроется {qc.format_dt_msk_compact(until_utc)} (МСК).\n"
        f"Изменил: {_actor_label(message)}"
    )


@router.message(Command("newautorule"))
async def cmd_newautorule(message: Message, db: AsyncSession) -> None:
    """Устанавливает кастомные правила автозакрытия набора для чата.

    Использование: /newautorule 0-1:n,1-18:1,18-999:15 или /newautorule default

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
    """
    if not message.text:
        return
    parts = message.text.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        await message.answer(
            "Укажите правила или default для сброса.\n"
            "Формат: 0-1:n,1-18:1,18-999:15 — интервалы [часов до занятия), "
            "после двоеточия часы до занятия когда закрыть набор, "
            "n — без автозакрытия."
        )
        return

    if rest.lower() in ("default", "сброс", "reset"):
        await queries_db.set_chat_autoclose_rules(db, message.chat.id, None)
        await db.flush()
        await message.answer(
            "Кастомные правила сброшены, снова действуют дефолтные."
        )
        return

    try:
        rules = parse_newautorule_line(rest)
        if not rules:
            await message.answer("Не удалось разобрать правила.")
            return
        await queries_db.set_chat_autoclose_rules(db, message.chat.id, rules)
        await db.flush()
        await message.answer(
            f"Сохранено правил: {len(rules)}. Дефолтные отключены для "
            "этого чата."
        )
    except ValueError as e:
        await message.answer(f"Ошибка: {e}")


@router.message(Command("group"))
async def cmd_group(message: Message, db: AsyncSession) -> None:
    """Управление постоянными группами участников чата (SuperVIP).

    Использование: /group @user1 @user2 ...

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
    """
    if not message.text:
        return

    chat = await queries_db.ensure_chat(
        db, message.chat.id, message.chat.title
    )
    if not has_supervip(effective_access(chat)):
        await message.answer("Нужна подписка SuperVIP (/pay).")
        return

    ids: list[int] = []
    for e in message.entities or []:
        if e.type == MessageEntityType.TEXT_MENTION and e.user:
            ids.append(e.user.id)

    tokens = message.text.split()
    for t in tokens:
        if t.startswith("@"):
            u = await queries_db.find_user_by_username(db, t[1:])
            if u and u.tg_id not in ids:
                ids.append(u.tg_id)

    if not ids:
        await message.answer(
            "Использование: /group @user1 @user2 ...\n"
            "Укажите участников группы (минимум один)."
        )
        return

    current_groups = list(chat.groups or [])
    new_groups = []
    for g in current_groups:
        filtered = [uid for uid in g if uid not in ids]
        if filtered:
            new_groups.append(filtered)
    new_groups.append(ids)

    chat.groups = new_groups
    flag_modified(chat, "groups")
    await db.flush()

    display_map = await queries_db.get_users_display_map(db, ids)
    names = [display_map.get(uid, str(uid)) for uid in ids]
    await message.answer(f"Группа создана: {', '.join(names)}")


@router.message(Command("insert"), F.reply_to_message)
async def cmd_insert(message: Message, db: AsyncSession, bot: Bot) -> None:
    """Вставляет участника на указанную позицию в сформированной очереди.

    Использование: ответом на список очереди: /insert @user N

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        bot: Экземпляр Telegram-бота.
    """
    if (
        not message.from_user
        or not message.text
        or not message.reply_to_message
    ):
        return

    chat = await queries_db.ensure_chat(db, message.chat.id)
    if not has_supervip(effective_access(chat)):
        await message.answer("Нужен SuperVIP.")
        return

    tokens = message.text.split()
    if len(tokens) < 3:
        await message.answer(
            "Использование: /insert @username N (ответом на список)"
        )
        return

    target_id, remaining = await _resolve_target_tg_id(
        message, db, tokens[1:2]
    )
    if target_id is None:
        await message.answer("Пользователь не найден в базе.")
        return

    pos_s = tokens[2]
    if not pos_s.isdigit():
        await message.answer("Укажите номер места N (число).")
        return
    pos = int(pos_s)

    rmid = message.reply_to_message.message_id
    q = await queries_db.get_queue_by_chat_message(
        db, message.chat.id, rmid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
        await message.answer("Нужна сформированная очередь.")
        return

    try:
        await queries_db.insert_into_formed_queue(db, q, target_id, pos)
        await db.flush()

        display_map = await queries_db.get_users_display_map(
            db, [message.from_user.id, target_id]
        )
        who_inserted = display_map.get(
            message.from_user.id, str(message.from_user.id)
        )
        target_name = display_map.get(target_id, str(target_id))

        await qc.refresh_queue_message(bot, db, q, message.reply_to_message)
        await message.answer(
            f"✅ {who_inserted} вставил {target_name} на место {pos}."
        )
    except ValueError as e:
        await message.answer(str(e))


@router.message(Command("last"), F.reply_to_message)
async def cmd_last_formed(
    message: Message, db: AsyncSession, bot: Bot
) -> None:
    """Вручную завершает очередь с отметкой последнего сдавшего участника.

    Использование: ответом на сформированную очередь: /last @username

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        bot: Экземпляр Telegram-бота.
    """
    if (
        not message.from_user
        or not message.text
        or not message.reply_to_message
    ):
        return

    chat = await queries_db.ensure_chat(db, message.chat.id)
    if not has_supervip(effective_access(chat)):
        await message.answer("Нужен SuperVIP.")
        return

    rmid = message.reply_to_message.message_id
    q = await queries_db.get_queue_by_chat_message(
        db, message.chat.id, rmid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
        await message.answer("Нужна сформированная очередь.")
        return

    tokens = message.text.split()
    if len(tokens) < 2:
        await message.answer(
            "Использование: /last @username (ответом на список)"
        )
        return

    target_id, _ = await _resolve_target_tg_id(message, db, tokens[1:2])
    if target_id is None:
        await message.answer("Пользователь не найден.")
        return

    target_display = await queries_db.get_user_display(db, target_id)

    try:
        await queries_db.complete_queue_last_submitter(db, q, target_id)
        await db.flush()
    except ValueError:
        await message.answer("Пользователь не в очереди.")
        return

    base = message.reply_to_message.text or ""
    done_suffix = (
        f"\n\n✅ Очередь завершена вручную. "
        f"Последним отметился: {target_display}"
    )
    await qc.safe_edit_text(
        bot=bot,
        chat_id=message.chat.id,
        message_id=rmid,
        text=base + done_suffix,
        reply_markup=None,
    )
    await message.answer("Готово.")


@router.message(Command("shuffle"), F.reply_to_message)
async def cmd_shuffle_formed(
    message: Message, db: AsyncSession, bot: Bot
) -> None:
    """Случайно перемешивает участников в сформированной очереди.

    Использование: ответом на сообщение со списком очереди: /shuffle

    Args:
        message: Входящее сообщение Telegram.
        db: Асинхронная сессия БД.
        bot: Экземпляр Telegram-бота.
    """
    if not message.reply_to_message:
        return

    mid = message.reply_to_message.message_id
    chat = await queries_db.ensure_chat(db, message.chat.id)
    if not has_supervip(effective_access(chat)):
        await message.answer("Нужна подписка SuperVIP.")
        return

    q = await queries_db.get_queue_by_chat_message(
        db, message.chat.id, mid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
        await message.answer("Нужна сформированная очередь.")
        return

    ex = dict(q.extra or {})
    order = list(ex.get("formed_order") or q.participants or [])
    random.shuffle(order)
    ex["formed_order"] = order
    q.participants = order
    flag_modified(q, "participants")
    queries_db.merge_extra(q, ex)
    await db.flush()

    await qc.refresh_queue_message(bot, db, q, message.reply_to_message)
    await message.answer("Порядок перемешан.")
