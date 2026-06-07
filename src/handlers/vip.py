"""Команды SuperVIP и настройки чата: /group, /insert, /last, /shuffle, /newautorule, /auto, /closeafter, /closebefore, /changename."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.enums import MessageEntityType
from aiogram.filters import Command
from aiogram.types import Message

from sqlalchemy.orm.attributes import flag_modified

from src.db.db import get_db
from src.db import queries as Q
from src.handlers import queue_common as qc
from src.services.subscription import effective_access, has_supervip
from src.utils.autoclose_rules import group_users_together, parse_newautorule_line
from src.utils.parse_queue import (
    parse_date_time_tokens,
    parse_duration_minutes,
    parse_subject_datetime_tokens,
)
from src.utils.telegram_text import text_without_text_mentions

logger = logging.getLogger(__name__)

router = Router()


def _actor_label(message: Message) -> str:
    if not message.from_user:
        return "неизвестный пользователь"
    if message.from_user.username:
        return f"@{message.from_user.username}"
    return message.from_user.full_name or str(message.from_user.id)


def _first_mention_tg_id(message: Message) -> int | None:
    for e in message.entities or []:
        if e.type == MessageEntityType.TEXT_MENTION and e.user:
            return e.user.id
    return None


def _resolve_target_tg_id(message: Message, db, tokens: list[str]) -> tuple[int | None, list[str]]:
    """Первый токен: числовой id, @username или упоминание в entities."""
    if not tokens:
        return None, []
    uid = _first_mention_tg_id(message)
    if uid is not None:
        return uid, tokens[1:]
    if tokens[0].isdigit():
        return int(tokens[0]), tokens[1:]
    if tokens[0].startswith("@"):
        u = Q.find_user_by_username(db, tokens[0][1:])
        if u:
            return u.tg_id, tokens[1:]
        return None, tokens
    return None, tokens


@router.message(Command("changename"))
async def cmd_changename(message: Message) -> None:
    if not message.from_user or not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /changename Иван Иванов")
        return
    new_name = parts[1].strip()
    with get_db() as db:
        Q.ensure_user(
            db,
            message.from_user.id,
            message.from_user.username,
            new_name,
        )
    await message.answer("Имя для отображения обновлено.")


@router.message(Command("auto"))
async def cmd_auto(message: Message) -> None:
    with get_db() as db:
        c = Q.ensure_chat(db, message.chat.id, message.chat.title)
        c.autoclose_enabled = not c.autoclose_enabled
        db.commit()
        on = c.autoclose_enabled
    await message.answer(
        "Автозакрытие по правилам по умолчанию для новых очередей: "
        + ("включено." if on else "выключено.")
    )


@router.message(Command("closeafter"))
async def cmd_closeafter(message: Message) -> None:
    if not message.reply_to_message or not message.text:
        await message.answer("Ответьте этой командой на сообщение с активной очередью (набор открыт).")
        return
    tok = message.text.split()
    if len(tok) < 2:
        await message.answer("Пример: /closeafter 30м  или  /closeafter 2ч")
        return
    dur = parse_duration_minutes(tok[1])
    if dur is None:
        await message.answer("Не удалось разобрать длительность. Используйте суффиксы 'м' или 'ч' (например, 30м, 2ч).")
        return
    now_utc = datetime.utcnow()
    until = now_utc + timedelta(minutes=dur)
    mid = message.reply_to_message.message_id
    cid = message.chat.id
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, cid, mid, for_update=True)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
            await message.answer("Очередь в статусе набора не найдена.")
            return
        q.close_at = until
        Q.merge_extra(db, q, {"manual_closeafter": True, "autoclose_disabled": False})
        subj_row = Q.get_subject_by_id(db, q.subject_id)
        if subj_row:
            ex = q.extra or {}
            head = qc.header_waiting(
                subj_row.subject_name,
                q.lesson_date,
                q.close_at,
                participants_count=len(q.participants or []),
                implicit_lesson=bool(ex.get("implicit_lesson", False)),
            )
            try:
                await message.bot.edit_message_text(
                    chat_id=cid,
                    message_id=mid,
                    text=head,
                    reply_markup=qc.kb_recruit(cid, mid),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
    await message.answer(
        f"Набор закроется не раньше {qc.format_dt_msk_compact(until)} (МСК).\n"
        f"Изменил: {_actor_label(message)}"
    )


@router.message(Command("closebefore"))
async def cmd_closebefore(message: Message) -> None:
    if not message.reply_to_message or not message.text:
        await message.answer("Ответьте этой командой на сообщение с активной очередью (набор открыт).")
        return
    tok = message.text.split()
    if len(tok) < 2:
        await message.answer("Пример: /closebefore 30м  или  /closebefore 2ч")
        return
    dur = parse_duration_minutes(tok[1])
    if dur is None:
        await message.answer("Не удалось разобрать длительность. Используйте суффиксы 'м' или 'ч' (например, 30м, 2ч).")
        return
    mid = message.reply_to_message.message_id
    cid = message.chat.id
    now_utc = datetime.utcnow()
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, cid, mid, for_update=True)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
            await message.answer("Очередь в статусе набора не найдена.")
            return
        until = q.lesson_date - timedelta(minutes=dur)
        if until <= now_utc:
            await message.answer(
                "Время закрытия уже в прошлом (или прямо сейчас). "
                "Уменьшите интервал для /closebefore или используйте /closeafter."
            )
            return
        q.close_at = until
        Q.merge_extra(
            db,
            q,
            {"manual_closebefore": True, "autoclose_disabled": False},
        )
        subj_row = Q.get_subject_by_id(db, q.subject_id)
        if subj_row:
            ex = q.extra or {}
            head = qc.header_waiting(
                subj_row.subject_name,
                q.lesson_date,
                q.close_at,
                participants_count=len(q.participants or []),
                implicit_lesson=bool(ex.get("implicit_lesson", False)),
            )
            try:
                await message.bot.edit_message_text(
                    chat_id=cid,
                    message_id=mid,
                    text=head,
                    reply_markup=qc.kb_recruit(cid, mid),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
    await message.answer(
        f"Набор закроется не позже {qc.format_dt_msk_compact(until)} (МСК) — "
        f"за {tok[1]} до времени очереди.\n"
        f"Изменил: {_actor_label(message)}"
    )


@router.message(Command("closeat"))
async def cmd_closeat(message: Message) -> None:
    if not message.reply_to_message or not message.text:
        await message.answer("Ответьте этой командой на сообщение с активной очередью (набор открыт).")
        return
    tok = message.text.split()[1:]
    if not tok:
        await message.answer("Пример: /closeat 15.04 14:30 или /closeat 14:30")
        return
        
    now_utc = datetime.utcnow()
    now_msk = now_utc + timedelta(hours=3)
    
    fake_parts = ["dummy"] + tok
    try:
        _, d, t = parse_subject_datetime_tokens(fake_parts, now_msk)
        if d is None and t is None:
             await message.answer("Не удалось разобрать дату/время.")
             return
        until_msk = parse_date_time_tokens(d, t, now_msk)
        until_utc = until_msk - timedelta(hours=3)
    except Exception as e:
        await message.answer(f"Ошибка формата времени: {e}")
        return
        
    if until_utc <= now_utc:
        await message.answer("Указанное время уже в прошлом.")
        return

    mid = message.reply_to_message.message_id
    cid = message.chat.id
    
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, cid, mid, for_update=True)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
            await message.answer("Очередь в статусе набора не найдена.")
            return
            
        q.close_at = until_utc
        Q.merge_extra(db, q, {"manual_closeafter": True, "autoclose_disabled": False})
        subj_row = Q.get_subject_by_id(db, q.subject_id)
        if subj_row:
            ex = q.extra or {}
            head = qc.header_waiting(
                subj_row.subject_name,
                q.lesson_date,
                q.close_at,
                participants_count=len(q.participants or []),
                implicit_lesson=bool(ex.get("implicit_lesson", False)),
            )
            try:
                await message.bot.edit_message_text(
                    chat_id=cid,
                    message_id=mid,
                    text=head,
                    reply_markup=qc.kb_recruit(cid, mid),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
                
    await message.answer(
        f"Набор закроется {qc.format_dt_msk_compact(until_utc)} (МСК).\n"
        f"Изменил: {_actor_label(message)}"
    )


@router.message(Command("newautorule"))
async def cmd_newautorule(message: Message) -> None:
    rest = message.text.split(maxsplit=1)[1] if message.text and len(message.text.split(maxsplit=1)) > 1 else ""
    rest = rest.strip()
    if not rest:
        await message.answer(
            "Укажите правила или `default` для сброса.\n"
            "Формат: `0-1:n,1-18:1,18-999:15` — интервалы [часов до занятия), "
            "после `:` часы до занятия когда закрыть набор, `n` — без автозакрытия."
        )
        return
    if rest.lower() in ("default", "сброс", "reset"):
        with get_db() as db:
            Q.set_chat_autoclose_rules(db, message.chat.id, None)
        await message.answer("Кастомные правила сброшены, снова действуют дефолтные.")
        return
    try:
        rules = parse_newautorule_line(rest)
        if not rules:
            await message.answer("Не удалось разобрать правила.")
            return
        with get_db() as db:
            Q.set_chat_autoclose_rules(db, message.chat.id, rules)
        await message.answer(f"Сохранено правил: {len(rules)}. Дефолтные отключены для этого чата.")
    except ValueError as e:
        await message.answer(f"Ошибка: {e}")


@router.message(Command("group"))
async def cmd_group(message: Message) -> None:
    """Управление постоянными группами чата: /group @user1 @user2 ..."""
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
        if not has_supervip(effective_access(chat)):
            await message.answer("Нужна подписка SuperVIP (/pay).")
            return
        
        ids: list[int] = []
        for e in message.entities or []:
            if e.type == MessageEntityType.TEXT_MENTION and e.user:
                ids.append(e.user.id)
        
        # Также пробуем найти по @username в тексте
        tokens = message.text.split()
        for t in tokens:
            if t.startswith("@"):
                u = Q.find_user_by_username(db, t[1:])
                if u and u.tg_id not in ids:
                    ids.append(u.tg_id)
        
        if not ids:
            await message.answer("Использование: /group @user1 @user2 ...\n"
                                 "Укажите участников группы (минимум один).")
            return
        
        current_groups = list(chat.groups or [])
        # Удаляем участников из других групп, если они там были
        new_groups = []
        for g in current_groups:
            filtered = [uid for uid in g if uid not in ids]
            if filtered:
                new_groups.append(filtered)
        new_groups.append(ids)
        
        chat.groups = new_groups
        flag_modified(chat, "groups")
        db.commit()
        
        names = [Q.get_user_display(db, uid) for uid in ids]
        await message.answer(f"Группа создана: {', '.join(names)}")


@router.message(Command("insert"), F.reply_to_message)
async def cmd_insert(message: Message, bot: Bot) -> None:
    """Вставить человека в сформированную очередь: /insert @user N"""
    if not message.from_user or not message.text:
        return
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id)
        if not has_supervip(effective_access(chat)):
            await message.answer("Нужен SuperVIP.")
            return
        
        tokens = message.text.split()
        if len(tokens) < 3:
            await message.answer("Использование: /insert @username N (ответом на список)")
            return
        
        handle = tokens[1].strip().lstrip("@")
        pos_s = tokens[2]
        if not pos_s.isdigit():
            await message.answer("Укажите номер места N (число).")
            return
        pos = int(pos_s)
        
        tu = Q.find_user_by_username(db, handle)
        target_id = tu.tg_id if tu else None
        # Если не нашли по юзернейму, может это упоминание?
        if target_id is None:
            target_id = _first_mention_tg_id(message)
            
        if target_id is None:
            await message.answer("Пользователь не найден в базе.")
            return

        rmid = message.reply_to_message.message_id
        q = Q.get_queue_by_chat_message(db, message.chat.id, rmid, for_update=True)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await message.answer("Нужна сформированная очередь.")
            return
            
        try:
            Q.insert_into_formed_queue(db, q, target_id, pos)
            who_inserted = Q.get_user_display(db, message.from_user.id)
            target_name = Q.get_user_display(db, target_id)
            await qc.refresh_queue_message(bot, db, q, message.reply_to_message)
            await message.answer(f"✅ {who_inserted} вставил {target_name} на место {pos}.")
        except ValueError as e:
            await message.answer(str(e))


@router.message(Command("last"), F.reply_to_message)
async def cmd_last_formed(message: Message, bot: Bot) -> None:
    """Вручную отметить последнего: /last @user (ответом на список)"""
    if not message.from_user:
        return
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id)
        if not has_supervip(effective_access(chat)):
            await message.answer("Нужен SuperVIP.")
            return
            
        rmid = message.reply_to_message.message_id
        q = Q.get_queue_by_chat_message(db, message.chat.id, rmid, for_update=True)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await message.answer("Нужна сформированная очередь.")
            return

        tokens = message.text.split()
        if len(tokens) < 2 or not tokens[1].startswith("@"):
            await message.answer("Использование: /last @username (ответом на список)")
            return
            
        handle = tokens[1].strip().lstrip("@")
        tu = Q.find_user_by_username(db, handle)
        if not tu:
            await message.answer("Пользователь не найден.")
            return
            
        target_id = tu.tg_id
        target_display = Q.get_user_display(db, target_id)

        try:
            Q.complete_queue_last_submitter(db, q, target_id)
        except ValueError:
            await message.answer("Пользователь не в очереди.")
            return

        base = message.reply_to_message.text or ""
        await message.reply_to_message.edit_text(
            base + f"\n\n✅ Очередь завершена (вручную). Последним отметился: {target_display}",
            reply_markup=None
        )
        await message.answer("Готово.")


@router.message(Command("shuffle"), F.reply_to_message)
async def cmd_shuffle_formed(message: Message, bot: Bot) -> None:
    """Перемешать очередь: /shuffle (ответом на список)"""
    mid = message.reply_to_message.message_id
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id)
        if not has_supervip(effective_access(chat)):
            await message.answer("Нужна подписка SuperVIP.")
            return
        q = Q.get_queue_by_chat_message(db, message.chat.id, mid, for_update=True)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await message.answer("Нужна сформированная очередь.")
            return
        
        ex = q.extra or {}
        order = list(ex.get("formed_order") or q.participants or [])
        random.shuffle(order)
        ex["formed_order"] = order
        q.participants = order
        flag_modified(q, "participants")
        Q.merge_extra(db, q, ex)
        await qc.refresh_queue_message(bot, db, q, message.reply_to_message)
    await message.answer("Порядок перемешан.")
