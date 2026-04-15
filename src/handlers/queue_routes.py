"""Очереди: /queue и все callback по записи."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.orm.attributes import flag_modified

from src.db.db import get_db
from src.db import queries as Q
from src.handlers import queue_common as qc
from src.handlers.queue_common import _from_msk_to_utc, _to_msk
from src.lexicon import LEXICON_RU
from src.services.subscription import effective_access, has_base_features
from src.services import queue_manager
from src.utils.autoclose_rules import compute_queue_close_at
from src.utils.parse_queue import lesson_datetime_from_command

logger = logging.getLogger(__name__)

_REFUSE_DONE = "Отказ отмечен в списке. Учтено +1 к попытке сдачи."

router = Router()


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    if message.text is None:
        return
    try:
        with get_db() as db:
            chat = Q.ensure_chat(db, message.chat.id, message.chat.title)
            acc = effective_access(chat)
            if not has_base_features(acc):
                await message.answer(
                    "❌ У этого чата нет активной подписки или пробного периода. "
                    "Оформите подписку командой /pay"
                )
                return

            created = datetime.utcnow()
            subj_name, lesson_dt, implicit_lesson = lesson_datetime_from_command(
                message.text, now=_to_msk(created)
            )
            # lesson_dt теперь в МСК (offset-aware), конвертируем в UTC (naive) для БД
            lesson_dt_utc = _from_msk_to_utc(lesson_dt).replace(tzinfo=None)
            
            if implicit_lesson:
                lesson_dt_utc = created
            
            subj = Q.get_or_create_subject(db, message.chat.id, subj_name)
            if Q.is_queue_duplicate(db, subj.id, message.chat.id, lesson_dt_utc):
                await message.answer(LEXICON_RU["/queue_error_message_UniqueConstraint"])
                return

            rules = chat.autoclose_rules
            if implicit_lesson:
                auto_close_utc = None
            else:
                # Расчет автозакрытия: передаем naive UTC, получаем naive UTC
                auto_close_utc = compute_queue_close_at(
                    created,
                    lesson_dt_utc,
                    autoclose_enabled=chat.autoclose_enabled,
                    custom_rules=rules,
                )
            
            extra: dict = {
                "autoclose_disabled": auto_close_utc is None,
                "lesson_dt_iso": lesson_dt_utc.isoformat(),
                "implicit_lesson": implicit_lesson,
            }

            head = qc.header_waiting(
                subj_name, lesson_dt_utc, auto_close_utc, implicit_lesson=implicit_lesson
            )
            sent = await message.answer(
                head,
                reply_markup=qc.kb_recruit(message.chat.id, 0),
            )
            mid = sent.message_id
            await sent.edit_reply_markup(reply_markup=qc.kb_recruit(message.chat.id, mid))

            Q.add_queue_row(
                db,
                subject_id=subj.id,
                chat_id=message.chat.id,
                message_id=mid,
                lesson_date=lesson_dt_utc,
                close_at=auto_close_utc,
                status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value,
                participants=[],
                extra=extra,
            )
    except ValueError as e:
        await message.answer(LEXICON_RU["/queue_error_message_ValueError"])
        logger.warning("queue parse: %s", e)


@router.callback_query(F.data.startswith("rf|"))
async def cb_refuse_ask(callback: CallbackQuery, bot: Bot) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text
    await msg.edit_reply_markup(reply_markup=None)
    await msg.edit_text(
        base + "\n\nВы уверены, что хотите отказаться от участия?",
        reply_markup=qc.kb_confirm_refuse(chat_id, mid),
    )
    await qc.schedule_confirm_reset(
        bot,
        chat_id,
        mid,
        base,
        (qc.kb_after_formed(chat_id, mid) 
         if "Список на" in base 
         else qc.kb_recruit(chat_id, mid)),
        "refuse",
    )


@router.callback_query(F.data.startswith("rfn|"))
async def cb_refuse_no(callback: CallbackQuery, bot: Bot) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    text = msg.text or ""
    base = text.split("\n\nВы уверены")[0]
    kb = (qc.kb_after_formed(chat_id, mid) 
          if "Список на" in base 
          else qc.kb_recruit(chat_id, mid))
    await msg.edit_text(base, reply_markup=kb)


@router.callback_query(F.data.startswith("rfy|"))
async def cb_refuse_yes(callback: CallbackQuery, bot: Bot) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q:
            return
        
        if q.status == qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            ex = dict(q.extra or {})
            order = list(ex.get("formed_order") or q.participants or [])
            indices = [i for i, x in enumerate(order) if x == tg_id]
            if not indices:
                await callback.answer("Вас нет в очереди.", show_alert=True)
                return
            li = indices[-1]
            slots = set(ex.get("refused_slot_indices", []) or [])
            slots.add(li)
            Q.merge_extra(db, q, {"refused_slot_indices": sorted(slots)})
            Q.increment_missed_for_tg_ids(db, [tg_id], q.subject_id)
            msg = callback.message
            if isinstance(msg, Message):
                await qc.refresh_queue_message(bot, db, q, msg)
            await callback.answer(_REFUSE_DONE, show_alert=False)
            return

        if q.status == qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
            if tg_id not in (q.participants or []):
                Q.add_participant(db, q, tg_id)
            ex = q.extra or {}
            rids = set(ex.get("refused_ids", []))
            rids.add(tg_id)
            ex["refused_ids"] = list(rids)
            Q.merge_extra(db, q, ex)
            Q.increment_missed_for_tg_ids(db, [tg_id], q.subject_id)
            await callback.answer(_REFUSE_DONE, show_alert=False)
            msg = callback.message
            if isinstance(msg, Message):
                await msg.edit_text(msg.text.split("\n\nВы уверены")[0], 
                                    reply_markup=qc.kb_recruit(chat_id, mid))


@router.message(Command("add"), F.reply_to_message)
async def cmd_add_to_formed(message: Message, bot: Bot) -> None:
    """Добавить временного участника в сформированную очередь: /add <имя> [N] (ответом на список)."""
    if not message.from_user or not message.text:
        return
    
    tokens = message.text.split()
    if len(tokens) < 2:
        await message.answer("Использование: /add <имя> [N] (ответом на список очереди)")
        return

    # Извлекаем имя и опционально N
    # Формат может быть: /add Иван 3  или /add Иван Иванов  или /add Иван
    arg_str = message.text.split(maxsplit=1)[1].strip()
    parts = arg_str.rsplit(maxsplit=1)
    
    target_name = arg_str
    pos: int | None = None
    
    if len(parts) == 2 and parts[1].isdigit():
        target_name = parts[0].strip()
        pos = int(parts[1])
    elif arg_str.isdigit():
        # Если ввели только число, считаем это ошибкой использования (нужно имя)
        await message.answer("Укажите имя временного участника: /add <имя> [N]")
        return

    with get_db() as db:
        rmid = message.reply_to_message.message_id
        cid = message.chat.id
        q = Q.get_queue_by_chat_message(db, cid, rmid)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await message.answer("Нужна сформированная очередь (ответьте на её сообщение).")
            return

        ex = dict(q.extra or {})
        order = list(ex.get("formed_order") or q.participants or [])

        if pos is None or pos < 1:
            order.append(target_name)
            real_pos = len(order)
        elif pos > len(order):
            order.append(target_name)
            real_pos = len(order)
        else:
            order.insert(pos - 1, target_name)
            real_pos = pos

        ex["formed_order"] = order
        q.participants = order
        flag_modified(q, "participants")
        Q.merge_extra(db, q, ex)
        await qc.refresh_queue_message(bot, db, q, message.reply_to_message)
        await message.answer(f"✅ Временный участник «{target_name}» добавлен на место {real_pos}.")


@router.callback_query(F.data.startswith("pa|"))
async def cb_participate_add(callback: CallbackQuery) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    with get_db() as db:
        Q.ensure_user(db, tg_id, un, rn)
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
            await callback.answer("Запись уже закрыта.", show_alert=True)
            return
        ex = q.extra or {}
        refused: set[int] = set(ex.get("refused_ids", []))
        if tg_id in refused:
            refused.discard(tg_id)
            ex["refused_ids"] = list(refused)
            Q.merge_extra(db, q, ex)
        rc = Q.add_participant(db, q, tg_id)
        if rc == -1:
            await callback.answer("Вы уже в списке.", show_alert=True)
            return
    await callback.answer("Вы записаны ✅")


@router.callback_query(F.data.startswith("pr|"))
async def cb_participate_remove(callback: CallbackQuery) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    tg_id = callback.from_user.id
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q:
            await callback.answer("Очередь не найдена.", show_alert=True)
            return
        Q.remove_participant(db, q, tg_id)
        ex = q.extra or {}
        rid = set(ex.get("refused_ids", []))
        if tg_id in rid:
            rid.discard(tg_id)
            ex["refused_ids"] = list(rid)
            Q.merge_extra(db, q, ex)
    await callback.answer("Участие отменено ❌")


@router.callback_query(F.data.startswith("sw|"))
async def cb_swap_request(callback: CallbackQuery, bot: Bot) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    with get_db() as db:
        Q.ensure_user(db, tg_id, un, rn)
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q:
            await callback.answer("Очередь не найдена.", show_alert=True)
            return
        if q.status == qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await callback.answer(
                "Ответьте на это сообщение со списком командой:\n/swap @username",
                show_alert=True,
            )
            return
        existing = Q.find_open_swap_for_message(db, mid)
        if existing:
            if existing.from_tg_id == tg_id:
                await callback.answer(
                    "Уже есть заявка. Ждём второго участника.",
                    show_alert=True,
                )
                return
            o1 = existing.from_tg_id
            o2 = tg_id
            if q.status == qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
                ids = list(q.participants or [])
            else:
                ids = list((q.extra or {}).get("formed_order") or q.participants or [])
            if o1 not in ids or o2 not in ids:
                await callback.answer("Оба должны быть в очереди.", show_alert=True)
                return
            i1, i2 = ids.index(o1), ids.index(o2)
            ids[i1], ids[i2] = ids[i2], ids[i1]
            if q.status == qc.QueueStatus.WAITING_FOR_PARTICIPANTS.value:
                q.participants = ids
                flag_modified(q, "participants")
                db.commit()
            else:
                ex = q.extra or {}
                ex["formed_order"] = ids
                q.participants = ids
                flag_modified(q, "participants")
                Q.merge_extra(db, q, ex)
            Q.complete_swap(db, existing, tg_id)
            try:
                await bot.delete_message(chat_id, existing.swap_message_id)  # type: ignore[arg-type]
            except Exception:
                pass
            await callback.answer("Места поменяны ✅")
            await qc.refresh_queue_message(bot, db, q, None)
            return
        txt = (
            f"🔀 Обмен местами: {Q.get_user_display(db, tg_id)} ищет пару.\n"
            f"Второй участник нажмите «Поменяться» в исходной очереди."
        )
        sent = await bot.send_message(chat_id, txt)
        Q.open_swap(db, chat_id, mid, q.subject_id, tg_id, sent.message_id)
    await callback.answer("Заявка создана")


@router.callback_query(F.data.startswith("cq|"))
async def cb_close_ask(callback: CallbackQuery, bot: Bot) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text
    await msg.edit_reply_markup(reply_markup=None)
    await msg.edit_text(
        base + "\n\nВы уверены, что хотите досрочно завершить набор?",
        reply_markup=qc.kb_confirm_close(chat_id, mid),
    )
    await qc.schedule_confirm_reset(
        bot,
        chat_id,
        mid,
        base,
        qc.kb_recruit(chat_id, mid),
        "close",
    )


@router.callback_query(F.data.startswith("cqn|"))
async def cb_close_no(callback: CallbackQuery, bot: Bot) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    text = msg.text or ""
    base = text.split("\n\nВы уверены")[0]
    await msg.edit_text(base, reply_markup=qc.kb_recruit(chat_id, mid))


@router.callback_query(F.data.startswith("cqy|"))
async def cb_close_yes(callback: CallbackQuery, bot: Bot) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q:
            return
        await qc.finalize_queue_core(bot, db, q, msg)
    user = callback.from_user
    if user:
        un = user.username
        who = f"@{un}" if un else f"id {user.id}"
        try:
            await bot.send_message(
                chat_id,
                f"⏹ Набор закрыт досрочно: {who}",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("dq|"))
async def cb_del_ask(callback: CallbackQuery, bot: Bot) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text.split("\n\nВы уверены")[0]
    await msg.edit_reply_markup(reply_markup=None)
    await msg.edit_text(
        base + "\n\nВы уверены, что хотите удалить эту очередь?",
        reply_markup=qc.kb_confirm_del(chat_id, mid),
    )
    await qc.schedule_confirm_reset(
        bot,
        chat_id,
        mid,
        base,
        qc.kb_recruit(chat_id, mid),
        "del",
    )


@router.callback_query(F.data.startswith("dqn|"))
async def cb_del_no(callback: CallbackQuery, bot: Bot) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    text = msg.text or ""
    base = text.split("\n\nВы уверены")[0]
    await msg.edit_text(base, reply_markup=qc.kb_recruit(chat_id, mid))


@router.callback_query(F.data.startswith("dqy|"))
async def cb_del_yes(callback: CallbackQuery, bot: Bot) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    user = callback.from_user
    un = user.username if user else None
    who = f"@{un}" if un else f"id {user.id if user else '?'}"
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if q:
            Q.delete_queue_row(db, q)
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await bot.send_message(chat_id, f"🗑 Запись на очередь удалена: {who}")
    except Exception:
        pass


@router.callback_query(F.data.startswith("lp|"))
async def cb_last_ask(callback: CallbackQuery, bot: Bot) -> None:
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    msg = callback.message
    if not isinstance(msg, Message) or not msg.text:
        return
    await msg.edit_text(
        msg.text + "\n\nВы уверены, что вы последний сдававший?",
        reply_markup=qc.kb_last_confirm(chat_id, mid),
    )


@router.callback_query(F.data.startswith("ln|"))
async def cb_last_no(callback: CallbackQuery) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    msg = callback.message
    if not isinstance(msg, Message):
        return
    text = msg.text or ""
    base = text.split("\n\nВы уверены")[0]
    await msg.edit_text(base, reply_markup=qc.kb_after_formed(chat_id, mid))


@router.callback_query(F.data.startswith("ly|"))
async def cb_last_yes(callback: CallbackQuery) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    msg = callback.message
    if not isinstance(msg, Message) or not callback.from_user:
        return
    tg_id = callback.from_user.id
    text = msg.text or ""
    base = text.split("\n\nВы уверены")[0]
    with get_db() as db:
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q:
            return
        try:
            Q.complete_queue_last_submitter(db, q, tg_id)
        except ValueError:
            await callback.answer("Вас нет в очереди.", show_alert=True)
            return
    fn = qc.escape_html_text(callback.from_user.full_name or "")
    un = callback.from_user.username
    who = (
        f"{fn} (@{qc.escape_html_text(un)})" if un else fn
    )
    await msg.edit_text(
        base + f"\n\n✅ Очередь завершена. Последним отметился: {who}",
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("ae|"))
async def cb_add_end(callback: CallbackQuery) -> None:
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    msg = callback.message
    if not isinstance(msg, Message) or not callback.from_user:
        return
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    with get_db() as db:
        Q.ensure_user(db, tg_id, un, rn)
        q = Q.get_queue_by_chat_message(db, chat_id, mid)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await callback.answer("Сейчас нельзя добавиться в конец.", show_alert=True)
            return
        ex = q.extra or {}
        order = list(ex.get("formed_order") or q.participants or [])
        if order and order[-1] == tg_id:
            await callback.answer(
                "Нельзя идти дважды подряд в конце очереди.",
                show_alert=True,
            )
            return
        order.append(tg_id)
        ex["formed_order"] = order
        q.participants = order
        flag_modified(q, "participants")
        Q.merge_extra(db, q, ex)
        Q.append_one_history_position(db, tg_id, q.subject_id, str(len(order)))
        await qc.refresh_queue_message(callback.bot, db, q, msg)
    await callback.answer("Вы в конце списка ✅")


@router.message(Command("swap"), F.reply_to_message)
async def cmd_swap_formed(message: Message, bot: Bot) -> None:
    """Ответ на список: /swap @username — заявка на обмен (сформированная очередь)."""
    if not message.from_user or not message.text:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Ответьте на сообщение со списком очереди и укажите:\n/swap @username"
        )
        return
    cmd = parts[0].split("@")[0].lower()
    if cmd != "/swap":
        return
    handle = parts[1].strip().lstrip("@")
    if not handle:
        await message.answer("Укажите username: /swap @username")
        return
    rmid = message.reply_to_message.message_id
    cid = message.chat.id
    uid = message.from_user.id

    with get_db() as db:
        chat = Q.ensure_chat(db, cid, message.chat.title)
        acc = effective_access(chat)
        if not has_base_features(acc):
            await message.answer(
                "❌ У этого чата нет активной подписки. Оформите /pay"
            )
            return

        q = Q.get_queue_by_chat_message(db, cid, rmid)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await message.answer("Нужна сформированная очередь (ответьте на её сообщение).")
            return
        tu = Q.find_user_by_username(db, handle)
        if not tu:
            await message.answer(
                "Пользователь с таким @username не найден в базе бота "
                "(он должен хотя бы раз взаимодействовать с ботом или быть в очереди)."
            )
            return
        target_id = tu.tg_id
        if target_id == uid:
            await message.answer("Нельзя меняться местами с самим собой.")
            return
        order = list((q.extra or {}).get("formed_order") or [])
        if uid not in order or target_id not in order:
            await message.answer("Оба участника должны быть в этом списке очереди.")
            return
        Q.delete_swaps_pending_for_queue(db, rmid)
        Q.ensure_user(db, uid, message.from_user.username, message.from_user.full_name)
        sw = Q.create_formed_swap_request(
            db,
            chat_id=cid,
            queue_message_id=rmid,
            subject_id=q.subject_id,
            from_tg_id=uid,
            to_tg_id=target_id,
            confirm_message_id=None,
        )
        sw_id = sw.id
        init_lbl = Q.get_user_display(db, uid)
        tgt_lbl = Q.get_user_display(db, target_id)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отменить",
                        callback_data=f"swu|{sw_id}",
                    ),
                    InlineKeyboardButton(
                        text="Поменяться",
                        callback_data=f"swp|{sw_id}",
                    ),
                ]
            ]
        )
        txt = f"🔀 {init_lbl} хочет поменяться с {tgt_lbl}."
    sent = await bot.send_message(message.chat.id, txt, reply_markup=kb)
    with get_db() as db:
        sw_row = Q.get_swap_request(db, sw_id)
        if sw_row:
            Q.set_swap_request_message_id(db, sw_row, sent.message_id)


@router.message(Command("swap"), ~F.reply_to_message)
async def cmd_swap_need_reply(message: Message) -> None:
    await message.answer(
        "Ответьте на сообщение со сформированным списком очереди и напишите:\n/swap @username"
    )


@router.callback_query(F.data.startswith("swu|"))
async def cb_formed_swap_cancel(callback: CallbackQuery, bot: Bot) -> None:
    """Отмена заявки — только инициатор (from_tg_id)."""
    if not callback.from_user:
        return
    parts = qc.split_cb(callback.data)
    if len(parts) < 2:
        return
    sw_id = int(parts[1])
    with get_db() as db:
        sw = Q.get_swap_request(db, sw_id)
        if not sw or sw.status != "await_accept":
            await callback.answer("Заявка недоступна.", show_alert=True)
            return
        if callback.from_user.id != sw.from_tg_id:
            await callback.answer(
                "Отменить может только тот, кто предложил обмен.", show_alert=True
            )
            return
        cht, smid = sw.chat_id, sw.swap_message_id
        Q.delete_swap_request_row(db, sw)
    if smid:
        try:
            await bot.delete_message(cht, smid)
        except Exception:
            pass
    await callback.answer("Заявка отменена")


@router.callback_query(F.data.startswith("swp|"))
async def cb_formed_swap_accept(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user:
        return
    parts = qc.split_cb(callback.data)
    if len(parts) < 2:
        return
    sw_id = int(parts[1])
    cht: int | None = None
    qmid: int | None = None
    smid: int | None = None
    with get_db() as db:
        sw = Q.get_swap_request(db, sw_id)
        if not sw or sw.status != "await_accept":
            await callback.answer("Заявка недоступна.", show_alert=True)
            return
        if callback.from_user.id != sw.to_tg_id:
            await callback.answer(
                "«Поменяться» может нажать только тот, кому предложили обмен.",
                show_alert=True,
            )
            return
        q = Q.get_queue_by_chat_message(db, sw.chat_id, sw.queue_message_id)
        if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT.value:
            await callback.answer("Очередь недоступна.", show_alert=True)
            return
        ex = dict(q.extra or {})
        order = list(ex.get("formed_order") or q.participants or [])
        if sw.from_tg_id not in order or sw.to_tg_id not in order:
            await callback.answer("Участники не в списке.", show_alert=True)
            return
        ia = max(i for i, x in enumerate(order) if x == sw.from_tg_id)
        ib = max(i for i, x in enumerate(order) if x == sw.to_tg_id)
        order[ia], order[ib] = order[ib], order[ia]
        q.participants = order
        flag_modified(q, "participants")
        Q.merge_extra(db, q, {"formed_order": order})
        # Синхронизируем последние позиции в queue_history для этой же очереди.
        # Историю не пересоздаём: меняем только последний элемент у двух участников.
        Q.sync_last_history_positions_after_swap(
            db,
            subject_id=q.subject_id,
            first_tg_id=sw.from_tg_id,
            first_new_pos_1based=ib + 1,
            second_tg_id=sw.to_tg_id,
            second_new_pos_1based=ia + 1,
            commit=False,
        )
        Q.mark_swap_done(db, sw)
        cht = sw.chat_id
        qmid = sw.queue_message_id
        smid = sw.swap_message_id
    if smid:
        try:
            await bot.delete_message(cht, smid)
        except Exception:
            pass
    if cht is not None and qmid is not None:
        with get_db() as db:
            q2 = Q.get_queue_by_chat_message(db, cht, qmid)
            if q2:
                await qc.refresh_queue_message(bot, db, q2, None)
    await callback.answer("Места поменяны ✅")
