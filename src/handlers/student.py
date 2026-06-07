"""Личка: старт, статистика, меню «на паре»; в группах: /who."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from src.db.db import get_db
from src.db import queries as Q
from src.lexicon import LEXICON_RU
from src.services.subscription import effective_access, has_base_features
from src.services.statistics_export import build_user_stats_text, stats_file_bytes

logger = logging.getLogger(__name__)

router = Router()

MENU_ATTENDANCE = "📍 Отметиться на паре"


def _attendance_kb(chat_id: int, message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ На паре",
                    callback_data=f"ph|{chat_id}|{message_id}|1",
                ),
                InlineKeyboardButton(
                    text="❌ Не на паре",
                    callback_data=f"ph|{chat_id}|{message_id}|0",
                ),
            ]
        ]
    )


def _presence_text(here_ids: list[int], db, subject_name: str | None = None) -> str:
    if subject_name:
        lines = [f"👥 Кто сейчас на паре по {subject_name}?", ""]
    else:
        lines = ["👥 Кто сейчас на паре?", ""]
    if not here_ids:
        lines.append("Пока никого.")
    else:
        for i, tid in enumerate(here_ids, 1):
            lines.append(f"{i}. {Q.get_user_display(db, tid)}")
    return "\n".join(lines)


def _extract_presence_subject(text: str | None) -> str | None:
    first = (text or "").splitlines()[0].strip() if (text or "").splitlines() else ""
    pref = "👥 Кто сейчас на паре по "
    if first.startswith(pref) and first.endswith("?"):
        subj = first[len(pref):-1].strip()
        return subj or None
    return None


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
        acc = effective_access(chat)
        is_active = has_base_features(acc)
        
    sub_status = "активна ✅" if is_active else "не активна ❌"
    text = LEXICON_RU["/start"].format(sub_status=sub_status)
    
    if message.chat.type == ChatType.PRIVATE:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=MENU_ATTENDANCE)]],
            resize_keyboard=True,
        )
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text)


@router.message(F.text == MENU_ATTENDANCE)
async def menu_attendance_hint(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE:
        return
    await message.answer(
        "В групповом чате отправьте команду /who — "
        "бот опубликует опрос «Кто сейчас на паре?»."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(LEXICON_RU["/help"])


@router.message(Command("subjects"))
async def cmd_subjects(message: Message) -> None:
    parts = (message.text or "").split()
    target_chat_id = message.chat.id

    if message.chat.type == ChatType.PRIVATE:
        if len(parts) < 2:
            await message.answer("В личке используйте: /subjects <id_чата>")
            return
        try:
            target_chat_id = int(parts[1])
        except ValueError:
            await message.answer("Некорректный id чата. Пример: /subjects -1001234567890")
            return

    with get_db() as db:
        Q.ensure_chat(db, target_chat_id, message.chat.title)
        subjects = Q.list_subject_names_for_chat(db, target_chat_id)
    if not subjects:
        await message.answer("Для этого чата пока нет созданных предметов.")
        return
    lines = [f"📚 Предметы чата {target_chat_id}:"]
    for i, name in enumerate(subjects, 1):
        lines.append(f"{i}. {name}")
    await message.answer("\n".join(lines))


@router.message(Command("stat"))
async def cmd_stat(message: Message, bot: Bot) -> None:
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Запросите статистику в личных сообщениях с ботом: /stat")
        return
    parts = (message.text or "").split()
    target_id = message.from_user.id if message.from_user else 0
    if len(parts) >= 2:
        if parts[1].isdigit():
            target_id = int(parts[1])
        elif parts[1].startswith("@"):
            with get_db() as db:
                u = Q.find_user_by_username(db, parts[1][1:])
                if u:
                    target_id = u.tg_id
                else:
                    await message.answer("Пользователь с таким @ не найден в базе.")
                    return
    with get_db() as db:
        text = build_user_stats_text(db, target_id)
    bio = stats_file_bytes(target_id, text)
    await bot.send_document(
        message.chat.id,
        BufferedInputFile(bio.getvalue(), filename=f"stat_{target_id}.txt"),
        caption=f"Статистика (tg_id={target_id})",
    )


@router.message(Command("attendance"))
async def cmd_attendance_obsolete(message: Message) -> None:
    await message.answer("Команда /attendance была заменена на /who.")


@router.message(Command("who"))
async def cmd_who(message: Message, bot: Bot) -> None:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("Команда /who только в группе.")
        return
    cid = message.chat.id
    tokens = (message.text or "").split(maxsplit=1)
    subject_name = tokens[1].strip() if len(tokens) > 1 else None
    with get_db() as db:
        Q.ensure_chat(db, cid, message.chat.title)
        empty = _presence_text([], db, subject_name)
    sent = await message.answer(empty)
    mid = sent.message_id
    await bot.edit_message_reply_markup(
        chat_id=cid,
        message_id=mid,
        reply_markup=_attendance_kb(cid, mid),
    )


@router.callback_query(F.data.startswith("ph|"))
async def cb_presence(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split("|")
    if len(parts) != 4:
        return
    _, cid_s, mid_s, mode = parts
    cid, mid = int(cid_s), int(mid_s)
    add = mode == "1"
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    with get_db() as db:
        Q.ensure_user(db, tg_id, un, rn)
        changed = Q.upsert_presence_here(db, cid, mid, tg_id, add)
        row = Q.get_presence_poll(db, cid, mid)
        here = list(row.here_tg_ids if row else [])
        subj = _extract_presence_subject(callback.message.text if isinstance(callback.message, Message) else None)
        text = _presence_text(here, db, subj)
    if not changed:
        await callback.answer("Уже учтено." if add else "Вас не было в списке.")
        return
    try:
        await bot.edit_message_text(
            chat_id=cid,
            message_id=mid,
            text=text,
            reply_markup=_attendance_kb(cid, mid),
        )
    except Exception as e:
        logger.warning("presence edit: %s", e)
    await callback.answer("Ок" if add else "Снято")


@router.message(Command("set_starosta"))
async def cmd_set_starosta(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return
        
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].startswith("@"):
        await message.answer("Формат: /set_starosta @username")
        return
    username = args[1][1:].strip()
    
    with get_db() as db:
        from src.db.init_db import User
        user = db.query(User).filter(User.tg_username.ilike(username)).first()
        if not user:
            await message.answer(f"Пользователь @{username} не найден в базе бота. Попросите его нажать /start в личных сообщениях с ботом.")
            return
            
        chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
        admins = list(chat.admins or [])
        if user.tg_id not in admins:
            admins.append(user.tg_id)
            chat.admins = admins
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(chat, "admins")
            db.commit()
            await message.answer(f"Пользователь @{username} назначен старостой группы.")
        else:
            await message.answer(f"Пользователь @{username} уже является старостой.")


@router.message(Command("rm_starosta"))
async def cmd_rm_starosta(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return
        
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].startswith("@"):
        await message.answer("Формат: /rm_starosta @username")
        return
    username = args[1][1:].strip()
    
    with get_db() as db:
        from src.db.init_db import User
        user = db.query(User).filter(User.tg_username.ilike(username)).first()
        if not user:
            await message.answer(f"Пользователь @{username} не найден в базе.")
            return
            
        chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
        admins = list(chat.admins or [])
        if user.tg_id in admins:
            admins.remove(user.tg_id)
            chat.admins = admins
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(chat, "admins")
            db.commit()
            await message.answer(f"Пользователь @{username} удалён из списка старост.")
        else:
            await message.answer("Этот пользователь не является старостой.")


@router.message(Command("confirm"))
async def cmd_confirm(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return
    
    if not message.reply_to_message:
        await message.answer("Ответьте этой командой на сообщение с очередью.")
        return
    
    from src.handlers.queue_common import subject_from_formed
    subj_name = subject_from_formed(message.reply_to_message.text)
    if not subj_name:
        await message.answer("Ответьте на сообщение со сформированной очередью (где есть текст «Список на...»).")
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].startswith("@"):
        await message.answer("Формат: /confirm @username")
        return
    username = args[1][1:].strip()
    
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
        admins = chat.admins or []
        if not admins:
            await message.answer("В этой группе не назначен староста. Назначьте его командой /set_starosta @username")
            return
            
        if message.from_user.id not in admins:
            await message.answer("Только староста может использовать эту команду.")
            return

        from src.db.init_db import User, Subject, SubmissionAttempt, Queue
        user = db.query(User).filter(User.tg_username.ilike(username)).first()
        if not user:
            await message.answer(f"Пользователь @{username} не найден в базе бота.")
            return
            
        subj = db.query(Subject).filter(Subject.chat_id == message.chat.id, Subject.subject_name == subj_name).first()
        if not subj:
            await message.answer("Предмет не найден в этой группе.")
            return
            
        q = db.query(Queue).filter(Queue.message_id == message.reply_to_message.message_id).first()
        if not q:
            await message.answer("Очередь не найдена в базе (возможно, сообщение было удалено).")
            return
            
        if q.status == "waiting_for_last_participant":
            p_ids = set((q.extra or {}).get("pardoned_tg_ids", []))
            p_ids.add(user.tg_id)
            Q.merge_extra(db, q, {"pardoned_tg_ids": list(p_ids)})
            db.commit()
            await message.answer(f"✅ Пользователь @{username} помилован. Когда очередь будет закрыта, он получит пропуск вместо штрафного подхода.")
            
        elif q.status == "completed":
            ex = q.extra or {}
            p_ids = ex.get("pardoned_tg_ids", [])
            if user.tg_id in p_ids:
                await message.answer("Этот пользователь уже был помилован.")
                return
                
            order = list(ex.get("formed_order") or q.participants or [])
            idx = -1
            for i, x in enumerate(order):
                if x == user.tg_id:
                    idx = i
            
            if idx == -1:
                await message.answer("Пользователь не участвовал в этой очереди.")
                return
                
            successful_slot_index = ex.get("successful_slot_index", -1)
            if idx > successful_slot_index and successful_slot_index != -1:
                await message.answer("Пользователь и так находился после последнего сдававшего, поэтому уже получил пропуск.")
                return
                
            row = db.query(SubmissionAttempt).filter(
                SubmissionAttempt.tg_id == user.tg_id, 
                SubmissionAttempt.subject_id == subj.id
            ).first()
            
            if not row or not row.history_position:
                await message.answer(f"У пользователя @{username} нет истории по этому предмету.")
                return
                
            pos_1based = str(idx + 1)
            hp = list(row.history_position)
            last_idx = -1
            for i in range(len(hp) - 1, -1, -1):
                if hp[i] == pos_1based:
                    last_idx = i
                    break
                    
            if last_idx != -1:
                hp[last_idx] = hp[last_idx] + "M"
                row.history_position = hp
                row.missed_attempts_count = int(row.missed_attempts_count or 0) + 1
                
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(row, "history_position")
                
                p_ids_set = set(p_ids)
                p_ids_set.add(user.tg_id)
                Q.merge_extra(db, q, {"pardoned_tg_ids": list(p_ids_set)})
                db.commit()
                
                await message.answer(f"✅ Успешный подход отменён задним числом. @{username} получил +1 к пропущенным слотам.")
            else:
                await message.answer("Не удалось найти соответствующую позицию в истории. Возможно, она уже была изменена.")
        else:
            await message.answer("Очередь находится в статусе, не подходящем для этой команды.")


@router.message(Command("king"))
async def cmd_king(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return
        
    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3 or not args[2].startswith("@"):
        await message.answer("Формат: /king Название_предмета @username")
        return
        
    subj_name = args[1].strip()
    username = args[2][1:].strip()
    
    with get_db() as db:
        from src.db.init_db import User, Subject
        user = db.query(User).filter(User.tg_username.ilike(username)).first()
        if not user:
            await message.answer(f"Пользователь @{username} не найден в базе бота.")
            return
            
        subj = db.query(Subject).filter(Subject.chat_id == message.chat.id, Subject.subject_name == subj_name).first()
        if not subj:
            await message.answer(f"Предмет «{subj_name}» не найден в этой группе.")
            return
            
        kings = list(subj.kings or [])
        if user.tg_id not in kings:
            kings.append(user.tg_id)
            subj.kings = kings
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(subj, "kings")
            db.commit()
            await message.answer(f"👑 Пользователь @{username} назначен командиром бригады по предмету «{subj_name}».")
        else:
            await message.answer(f"Пользователь @{username} уже является королём по этому предмету.")


@router.message(Command("unking"))
async def cmd_unking(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return
        
    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3 or not args[2].startswith("@"):
        await message.answer("Формат: /unking Название_предмета @username")
        return
        
    subj_name = args[1].strip()
    username = args[2][1:].strip()
    
    with get_db() as db:
        from src.db.init_db import User, Subject
        user = db.query(User).filter(User.tg_username.ilike(username)).first()
        if not user:
            await message.answer(f"Пользователь @{username} не найден в базе бота.")
            return
            
        subj = db.query(Subject).filter(Subject.chat_id == message.chat.id, Subject.subject_name == subj_name).first()
        if not subj:
            await message.answer(f"Предмет «{subj_name}» не найден в этой группе.")
            return
            
        kings = list(subj.kings or [])
        if user.tg_id in kings:
            kings.remove(user.tg_id)
            subj.kings = kings
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(subj, "kings")
            db.commit()
            await message.answer(f"Пользователь @{username} лишён статуса короля по предмету «{subj_name}».")
        else:
            await message.answer("Этот пользователь не является королём по этому предмету.")


@router.message(Command("rm"))
async def cmd_rm_queue(message: Message, bot: Bot) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return
        
    if not message.reply_to_message:
        await message.answer("Ответьте этой командой на сообщение с очередью.")
        return
        
    with get_db() as db:
        chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
        admins = chat.admins or []
        if not admins:
            await message.answer("В этой группе не назначен староста. Назначить можно командой: /set_starosta @username")
            return
            
        if message.from_user.id not in admins:
            await message.answer("Только староста может удалить очередь.")
            return
            
        from src.db.init_db import Queue, SubmissionAttempt
        q = db.query(Queue).filter(Queue.message_id == message.reply_to_message.message_id).first()
        if not q:
            await message.answer("Очередь не найдена в БД.")
            return
            
        if q.status == "waiting_for_participants":
            db.delete(q)
            db.commit()
            try:
                await bot.edit_message_text(
                    chat_id=q.chat_id,
                    message_id=q.message_id,
                    text=f"❌ Очередь отменена и удалена старостой @{message.from_user.username or message.from_user.first_name}.",
                    reply_markup=None
                )
            except Exception:
                pass
            await message.answer(f"Очередь удалена @{message.from_user.username or message.from_user.first_name}.")
            return
            
        ex = q.extra or {}
        order = list(ex.get("formed_order") or q.participants or [])
        pardoned = ex.get("pardoned_tg_ids", [])
        successful_slot_index = ex.get("successful_slot_index", -1)
        
        # Fetch all relevant rows in one query to avoid N+1
        uids = [entry for entry in order if isinstance(entry, int)]
        rows = db.query(SubmissionAttempt).filter(
            SubmissionAttempt.tg_id.in_(uids),
            SubmissionAttempt.subject_id == q.subject_id
        ).all()
        rows_by_uid = {r.tg_id: r for r in rows}
        
        # Reverse history
        for idx, entry in enumerate(order):
            if not isinstance(entry, int):
                continue
                
            row = rows_by_uid.get(entry)
            if not row or not row.history_position:
                continue
                
            hp = list(row.history_position)
            target = str(idx + 1)
            target_m = target + "M"
            
            last_idx = -1
            found_target = None
            
            if q.status == "waiting_for_last_participant":
                expected_target = target
            elif q.status == "completed":
                if idx <= successful_slot_index and entry not in pardoned:
                    expected_target = target
                else:
                    expected_target = target_m
            else:
                expected_target = target
                
            for i in range(len(hp) - 1, -1, -1):
                if hp[i] == expected_target:
                    last_idx = i
                    found_target = hp[i]
                    break
                    
            if last_idx == -1:
                other_target = target_m if expected_target == target else target
                for i in range(len(hp) - 1, -1, -1):
                    if hp[i] == other_target:
                        last_idx = i
                        found_target = hp[i]
                        break
            
            if last_idx != -1:
                hp.pop(last_idx)
                row.history_position = hp
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(row, "history_position")
                
                if found_target.endswith("M") and q.status == "completed":
                    row.missed_attempts_count = max(0, int(row.missed_attempts_count or 0) - 1)
                    
        db.delete(q)
        db.commit()
        
        try:
            await bot.edit_message_text(
                chat_id=q.chat_id,
                message_id=q.message_id,
                text=f"❌ Очередь удалена старостой @{message.from_user.username or message.from_user.first_name}. Все записи в статистике отменены.",
                reply_markup=None
            )
        except Exception:
            pass
            
        await message.answer(f"Очередь удалена @{message.from_user.username or message.from_user.first_name}, статистика участников восстановлена.")
