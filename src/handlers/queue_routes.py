"""Маршруты очередей: создание через /queue и интерактивные действия."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db import queries as queries_db
from src.handlers import queue_common as qc
from src.handlers.queue_common import _from_msk_to_utc, _to_msk
from src.lexicon import LEXICON_RU
from src.services.subscription import effective_access, has_base_features
from src.utils.autoclose_rules import compute_queue_close_at
from src.utils.parse_queue import lesson_datetime_from_command

logger = logging.getLogger(__name__)

_REFUSE_DONE = "Отказ отмечен в списке. Учтено +1 к попытке сдачи."

router = Router()


@router.message(Command("queue"))
async def cmd_queue(message: Message, session: AsyncSession) -> None:
    """Обрабатывает команду /queue для создания новой интерактивной очереди.

    Парсит параметры расписания, проверяет подписку чата, уникальность очереди,
    рассчитывает время автозакрытия и отправляет сообщение с кнопками записи.

    Args:
        message: Входящее сообщение aiogram с командой /queue.
        session: Асинхронная сессия базы данных.
    """
    if message.text is None:
        return
    try:
        db = session
        chat = await queries_db.ensure_chat(
            db, message.chat.id, message.chat.title
        )
        acc = effective_access(chat)
        if not has_base_features(acc):
            await message.answer(
                "❌ У этого чата нет активной подписки или пробного периода. "
                "Оформите подписку командой /pay"
            )
            return

        now_utc = datetime.now(UTC).replace(tzinfo=None)
        subj_name, lesson_dt, implicit_lesson = lesson_datetime_from_command(
            message.text, now=_to_msk(now_utc)
        )

        lesson_dt_utc = _from_msk_to_utc(lesson_dt).replace(tzinfo=None)

        if implicit_lesson:
            lesson_dt_utc = now_utc

        subj = await queries_db.get_or_create_subject(
            db, message.chat.id, subj_name
        )
        if await queries_db.is_queue_duplicate(
            db, subj.id, message.chat.id, lesson_dt_utc
        ):
            await message.answer(
                LEXICON_RU["/queue_error_message_UniqueConstraint"]
            )
            return

        rules = chat.autoclose_rules
        if implicit_lesson:
            auto_close_utc = None
        else:
            auto_close_utc = compute_queue_close_at(
                now_utc,
                lesson_dt_utc,
                autoclose_enabled=chat.autoclose_enabled,
                custom_rules=rules,
            )

        extra: dict[str, Any] = {
            "autoclose_disabled": auto_close_utc is None,
            "lesson_dt_iso": lesson_dt_utc.isoformat(),
            "implicit_lesson": implicit_lesson,
        }

        head = qc.header_waiting(
            subj_name,
            lesson_dt_utc,
            auto_close_utc,
            participants_count=0,
            implicit_lesson=implicit_lesson,
        )
        sent = await message.answer(
            head,
            reply_markup=qc.kb_recruit(message.chat.id, 0),
        )
        mid = sent.message_id
        await sent.edit_reply_markup(
            reply_markup=qc.kb_recruit(message.chat.id, mid)
        )

        await queries_db.add_queue_row(
            db,
            subject_id=subj.id,
            chat_id=message.chat.id,
            message_id=mid,
            lesson_date=lesson_dt_utc,
            close_at=auto_close_utc,
            status=qc.QueueStatus.WAITING_FOR_PARTICIPANTS,
            participants=[],
            extra=extra,
        )
    except ValueError as e:
        await message.answer(LEXICON_RU["/queue_error_message_ValueError"])
        logger.warning("queue parse: %s", e)


@router.callback_query(F.data.startswith("rf|"))
async def cb_refuse_ask(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Запрашивает подтверждение отказа от участия в очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
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
        (
            qc.kb_after_formed(chat_id, mid)
            if "Список на" in base
            else qc.kb_recruit(chat_id, mid)
        ),
        "refuse",
    )


@router.callback_query(F.data.startswith("rfn|"))
async def cb_refuse_no(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Отменяет отказ от участия и восстанавливает исходную клавиатуру.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text.split("\n\nВы уверены")[0]
    kb = (
        qc.kb_after_formed(chat_id, mid)
        if "Список на" in base
        else qc.kb_recruit(chat_id, mid)
    )
    await msg.edit_text(base, reply_markup=kb)


@router.callback_query(F.data.startswith("rfy|"))
async def cb_refuse_yes(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Подтверждает отказ от участия и отмечает его в списке очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    if not callback.from_user:
        return
    tg_id = callback.from_user.id
    db = session
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if not q:
        return

    if q.status == qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        ex = dict(q.extra or {})
        order = list(q.participants or [])
        indices = [i for i, x in enumerate(order) if x == tg_id]
        if not indices:
            await callback.answer("Вас нет в очереди.", show_alert=True)
            return
        li = indices[-1]
        slots = set(ex.get("refused_slot_indices", []) or [])
        slots.add(li)
        queries_db.merge_extra(q, {"refused_slot_indices": sorted(slots)})
        msg = callback.message
        if isinstance(msg, Message):
            await qc.refresh_queue_message(bot, db, q, msg)
        await callback.answer(_REFUSE_DONE, show_alert=False)
        return

    if q.status == qc.QueueStatus.WAITING_FOR_PARTICIPANTS:
        try:
            await queries_db.add_participant(db, q, tg_id)
        except queries_db.ParticipantAlreadyExistsError:
            pass
        ex = dict(q.extra or {})
        rids = set(ex.get("refused_ids", []))
        rids.add(tg_id)
        ex["refused_ids"] = list(rids)
        queries_db.merge_extra(q, ex)
        await callback.answer(_REFUSE_DONE, show_alert=False)
        msg = callback.message
        if isinstance(msg, Message) and msg.text is not None:
            await msg.edit_text(
                msg.text.split("\n\nВы уверены")[0],
                reply_markup=qc.kb_recruit(chat_id, mid),
            )


@router.message(Command("add"), F.reply_to_message)
async def cmd_add_to_formed(
    message: Message, bot: Bot, session: AsyncSession
) -> None:
    """Добавляет временного участника в сформированную очередь.

    Формат команды: /add <имя> [позиция N] ответом на список очереди.

    Args:
        message: Входящее сообщение aiogram.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if (
        not message.from_user
        or not message.text
        or not message.reply_to_message
    ):
        return

    tokens = message.text.split()
    if len(tokens) < 2:
        await message.answer(
            "Использование: /add <имя> [N] (ответом на список очереди)"
        )
        return

    arg_str = message.text.split(maxsplit=1)[1].strip()
    parts = arg_str.rsplit(maxsplit=1)

    target_name = arg_str
    pos: int | None = None

    if len(parts) == 2 and parts[1].isdigit():
        target_name = parts[0].strip()
        pos = int(parts[1])
    elif arg_str.isdigit():
        await message.answer(
            "Укажите имя временного участника: /add <имя> [N]"
        )
        return

    db = session
    rmid = message.reply_to_message.message_id
    cid = message.chat.id
    q = await queries_db.get_queue_by_chat_message(
        db, cid, rmid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await message.answer(
            "Нужна сформированная очередь (ответьте на сообщение очереди)."
        )
        return

    ex = dict(q.extra or {})
    order = list(q.participants or [])
    temp_names = ex.get("temp_names", {})

    temp_id = -len(temp_names) - 1
    while str(temp_id) in temp_names or temp_id in order:
        temp_id -= 1

    temp_names[str(temp_id)] = target_name
    ex["temp_names"] = temp_names

    if pos is None or pos < 1 or pos > len(order):
        order.append(temp_id)
        real_pos = len(order)
    else:
        order.insert(pos - 1, temp_id)
        real_pos = pos

    q.participants = order
    flag_modified(q, "participants")
    queries_db.merge_extra(q, ex)
    await qc.refresh_queue_message(bot, db, q, message.reply_to_message)
    await message.answer(
        f"✅ Временный участник «{target_name}» добавлен на место {real_pos}."
    )


@router.callback_query(F.data.startswith("pa|"))
async def cb_participate_add(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Записывает пользователя в очередь в статусе набора.

    Args:
        callback: Входящий callback-запрос.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    db = session
    await queries_db.ensure_user(db, tg_id, un, rn)
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS:
        await callback.answer("Запись уже закрыта.", show_alert=True)
        return
    ex = dict(q.extra or {})
    refused: set[int] = set(ex.get("refused_ids", []))
    if tg_id in refused:
        refused.discard(tg_id)
        ex["refused_ids"] = list(refused)
        queries_db.merge_extra(q, ex)
    try:
        await queries_db.add_participant(db, q, tg_id)
    except queries_db.ParticipantAlreadyExistsError:
        await callback.answer("Вы уже в списке.", show_alert=True)
        return
    subj = await queries_db.get_subject_by_id(db, q.subject_id)
    if subj and isinstance(callback.message, Message):
        ex_extra = q.extra or {}
        head = qc.header_waiting(
            subj.subject_name,
            q.lesson_date,
            q.close_at,
            participants_count=len(q.participants or []),
            implicit_lesson=bool(ex_extra.get("implicit_lesson", False)),
        )
        await callback.message.edit_text(
            head,
            reply_markup=qc.kb_recruit(chat_id, mid),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer("Вы записаны ✅")


@router.callback_query(F.data.startswith("pr|"))
async def cb_participate_remove(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Удаляет пользователя из очереди на этапе набора.

    Args:
        callback: Входящий callback-запрос.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    tg_id = callback.from_user.id
    db = session
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if not q:
        await callback.answer("Очередь не найдена.", show_alert=True)
        return
    if q.status != qc.QueueStatus.WAITING_FOR_PARTICIPANTS:
        await callback.answer("Запись уже закрыта.", show_alert=True)
        return
    await queries_db.remove_participant(db, q, tg_id)
    ex = dict(q.extra or {})
    rid = set(ex.get("refused_ids", []))
    if tg_id in rid:
        rid.discard(tg_id)
        ex["refused_ids"] = list(rid)
        queries_db.merge_extra(q, ex)
    subj = await queries_db.get_subject_by_id(db, q.subject_id)
    if subj and isinstance(callback.message, Message):
        ex_extra = q.extra or {}
        head = qc.header_waiting(
            subj.subject_name,
            q.lesson_date,
            q.close_at,
            participants_count=len(q.participants or []),
            implicit_lesson=bool(ex_extra.get("implicit_lesson", False)),
        )
        await callback.message.edit_text(
            head,
            reply_markup=qc.kb_recruit(chat_id, mid),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer("Участие отменено ❌")


@router.callback_query(F.data.startswith("sw|"))
async def cb_swap_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Инициирует либо завершает взаимный обмен местами на этапе набора.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    db = session
    await queries_db.ensure_user(db, tg_id, un, rn)
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if not q:
        await callback.answer("Очередь не найдена.", show_alert=True)
        return
    if q.status == qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await callback.answer(
            "Ответьте на это сообщение со списком командой:\n/swap @username",
            show_alert=True,
        )
        return
    existing = await queries_db.find_open_swap_for_message(db, mid)
    if existing:
        if existing.from_tg_id == tg_id:
            await callback.answer(
                "Уже есть заявка. Ждём второго участника.",
                show_alert=True,
            )
            return
        o1 = existing.from_tg_id
        o2 = tg_id
        ids = list(q.participants or [])
        if o1 not in ids or o2 not in ids:
            await callback.answer(
                "Оба участника должны быть в очереди.", show_alert=True
            )
            return
        i1, i2 = ids.index(o1), ids.index(o2)
        ids[i1], ids[i2] = ids[i2], ids[i1]
        q.participants = ids
        flag_modified(q, "participants")
        await db.flush()
        await queries_db.complete_swap(db, existing, tg_id)
        if existing.swap_message_id is not None:
            try:
                await bot.delete_message(chat_id, existing.swap_message_id)
            except TelegramAPIError as exc:
                logger.debug("Не удалось удалить сообщение обмена: %s", exc)
        await callback.answer("Места поменяны ✅")
        await qc.refresh_queue_message(bot, db, q, None)
        return
    user_label = await queries_db.get_user_display(db, tg_id)
    txt = (
        f"🔀 Обмен местами: {user_label} ищет пару.\n"
        f"Второй участник нажмите «Поменяться» в исходной очереди."
    )
    sent = await bot.send_message(chat_id, txt)
    await queries_db.open_swap(
        db, chat_id, mid, q.subject_id, tg_id, sent.message_id
    )
    await callback.answer("Заявка создана")


@router.callback_query(F.data.startswith("cq|"))
async def cb_close_ask(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Запрашивает подтверждение досрочного завершения набора очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
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
async def cb_close_no(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Отменяет досрочное закрытие набора в очередь.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text.split("\n\nВы уверены")[0]
    await msg.edit_text(base, reply_markup=qc.kb_recruit(chat_id, mid))


@router.callback_query(F.data.startswith("cqy|"))
async def cb_close_yes(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Подтверждает досрочное завершение набора и формирует список очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    db = session
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
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
        except TelegramAPIError as exc:
            logger.debug(
                "Не удалось отправить сообщение об остановке: %s", exc
            )


@router.callback_query(F.data.startswith("dq|"))
async def cb_del_ask(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Запрашивает подтверждение удаления очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
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
async def cb_del_no(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Отменяет удаление очереди и восстанавливает клавиатуру.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text.split("\n\nВы уверены")[0]
    await msg.edit_text(base, reply_markup=qc.kb_recruit(chat_id, mid))


@router.callback_query(F.data.startswith("dqy|"))
async def cb_del_yes(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Удаляет очередь и связанное сообщение по подтверждению.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    qc.cancel_pending(chat_id, mid)
    msg = callback.message
    if not isinstance(msg, Message):
        return
    user = callback.from_user
    un = user.username if user else None
    who = f"@{un}" if un else f"id {user.id if user else '?'}"
    db = session
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if q:
        await queries_db.delete_queue_row(db, q)
    try:
        await msg.delete()
    except TelegramAPIError as exc:
        logger.debug("Не удалось удалить сообщение очереди: %s", exc)
    try:
        await bot.send_message(chat_id, f"🗑 Запись на очередь удалена: {who}")
    except TelegramAPIError as exc:
        logger.debug("Не удалось отправить сообщение об удалении: %s", exc)


@router.callback_query(F.data.startswith("lp|"))
async def cb_last_ask(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Запрашивает подтверждение статуса последнего сдавшего участника.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    _, cid_s, mid_s = qc.split_cb(callback.data)
    chat_id, mid = int(cid_s), int(mid_s)
    db = session
    q = await queries_db.get_queue_by_chat_message(db, chat_id, mid)
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await callback.answer(
            "Очередь еще не сформирована или уже завершена.", show_alert=True
        )
        return
    msg = callback.message
    if not isinstance(msg, Message) or not msg.text:
        return
    await msg.edit_text(
        msg.text + "\n\nВы уверены, что вы последний сдававший?",
        reply_markup=qc.kb_last_confirm(chat_id, mid),
    )


@router.callback_query(F.data.startswith("ln|"))
async def cb_last_no(callback: CallbackQuery, session: AsyncSession) -> None:
    """Отменяет подтверждение статуса последнего сдавшего.

    Args:
        callback: Входящий callback-запрос.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    base = msg.text.split("\n\nВы уверены")[0]
    await msg.edit_text(base, reply_markup=qc.kb_after_formed(chat_id, mid))


@router.callback_query(F.data.startswith("ly|"))
async def cb_last_yes(callback: CallbackQuery, session: AsyncSession) -> None:
    """Фиксирует последнего сдавшего и завершает очередь со штрафами.

    Args:
        callback: Входящий callback-запрос.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    tg_id = callback.from_user.id
    base = msg.text.split("\n\nВы уверены")[0]
    db = session
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if not q:
        return
    if q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await callback.answer(
            "Очередь еще не сформирована или уже завершена.", show_alert=True
        )
        return
    try:
        await queries_db.complete_queue_last_submitter(db, q, tg_id)
    except ValueError:
        await callback.answer("Вас нет в очереди.", show_alert=True)
        return
    fn = qc.escape_html_text(callback.from_user.full_name or "")
    un = callback.from_user.username
    who = f"{fn} (@{qc.escape_html_text(un)})" if un else fn
    await msg.edit_text(
        base + f"\n\n✅ Очередь завершена. Последним отметился: {who}",
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("ae|"))
async def cb_add_end(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Добавляет пользователя в конец сформированной очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    parts = qc.split_cb(callback.data)
    chat_id, mid = int(parts[1]), int(parts[2])
    msg = callback.message
    if not isinstance(msg, Message):
        return
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    db = session
    await queries_db.ensure_user(db, tg_id, un, rn)
    q = await queries_db.get_queue_by_chat_message(
        db, chat_id, mid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await callback.answer(
            "Сейчас нельзя добавиться в конец.", show_alert=True
        )
        return
    order = list(q.participants or [])
    if order and order[-1] == tg_id:
        await callback.answer(
            "Нельзя идти дважды подряд в конце очереди.",
            show_alert=True,
        )
        return
    order.append(tg_id)
    q.participants = order
    flag_modified(q, "participants")
    await queries_db.append_one_history_position(
        db, tg_id, q.subject_id, str(len(order))
    )
    await qc.refresh_queue_message(bot, db, q, msg)
    await callback.answer("Вы в конце списка ✅")


@router.message(Command("swap"), F.reply_to_message)
async def cmd_swap_formed(
    message: Message, bot: Bot, session: AsyncSession
) -> None:
    """Создает заявку на обмен местами в сформированной очереди.

    Формат команды: /swap @username ответом на список очереди.

    Args:
        message: Входящее сообщение aiogram.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if (
        not message.from_user
        or not message.text
        or not message.reply_to_message
    ):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Ответьте на сообщение со списком очереди и укажите:\n"
            "/swap @username"
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

    db = session
    chat = await queries_db.ensure_chat(db, cid, message.chat.title)
    acc = effective_access(chat)
    if not has_base_features(acc):
        await message.answer(
            "❌ У этого чата нет активной подписки. Оформите /pay"
        )
        return

    q = await queries_db.get_queue_by_chat_message(
        db, cid, rmid, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await message.answer(
            "Нужна сформированная очередь (ответьте на сообщение очереди)."
        )
        return
    tu = await queries_db.find_user_by_username(db, handle)
    if not tu:
        await message.answer(
            "Пользователь с таким @username не найден в базе бота."
        )
        return
    target_id = tu.tg_id
    if target_id == uid:
        await message.answer("Нельзя меняться местами с самим собой.")
        return
    order = list(q.participants or [])
    if uid not in order or target_id not in order:
        await message.answer(
            "Оба участника должны быть в этом списке очереди."
        )
        return
    await queries_db.delete_swaps_pending_for_queue(db, rmid)
    await queries_db.ensure_user(
        db, uid, message.from_user.username, message.from_user.full_name
    )
    sw = await queries_db.create_formed_swap_request(
        db,
        chat_id=cid,
        queue_message_id=rmid,
        subject_id=q.subject_id,
        from_tg_id=uid,
        to_tg_id=target_id,
        confirm_message_id=None,
    )
    sw_id = sw.id
    init_lbl = await queries_db.get_user_display(db, uid)
    tgt_lbl = await queries_db.get_user_display(db, target_id)
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
    sw_row = await queries_db.get_swap_request(db, sw_id)
    if sw_row:
        await queries_db.set_swap_request_message_id(
            db, sw_row, sent.message_id
        )


@router.message(Command("swap"), ~F.reply_to_message)
async def cmd_swap_need_reply(message: Message, session: AsyncSession) -> None:
    """Напоминает о необходимости ответа на сообщение очереди при вызове /swap.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    await message.answer(
        "Ответьте на сообщение со сформированным списком очереди и "
        "напишите:\n/swap @username"
    )


@router.callback_query(F.data.startswith("swu|"))
async def cb_formed_swap_cancel(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Отменяет заявку на обмен инициатором (from_tg_id).

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    parts = qc.split_cb(callback.data)
    if len(parts) < 2:
        return
    sw_id = int(parts[1])
    db = session
    sw = await queries_db.get_swap_request(db, sw_id)
    if not sw or sw.status != "await_accept":
        await callback.answer("Заявка недоступна.", show_alert=True)
        return
    if callback.from_user.id != sw.from_tg_id:
        await callback.answer(
            "Отменить может только тот, кто предложил обмен.", show_alert=True
        )
        return
    cht, smid = sw.chat_id, sw.swap_message_id
    await queries_db.delete_swap_request_row(db, sw)
    if smid:
        try:
            await bot.delete_message(cht, smid)
        except TelegramAPIError as exc:
            logger.debug("Не удалось удалить сообщение заявки: %s", exc)
    await callback.answer("Заявка отменена")


@router.callback_query(F.data.startswith("swp|"))
async def cb_formed_swap_accept(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Принимает заявку на обмен местами и обновляет порядок в очереди.

    Args:
        callback: Входящий callback-запрос.
        bot: Экземпляр Telegram бота.
        session: Асинхронная сессия БД.
    """
    if not callback.data or not callback.from_user:
        return
    parts = qc.split_cb(callback.data)
    if len(parts) < 2:
        return
    sw_id = int(parts[1])
    db = session
    sw = await queries_db.get_swap_request(db, sw_id)
    if not sw or sw.status != "await_accept":
        await callback.answer("Заявка недоступна.", show_alert=True)
        return
    if callback.from_user.id != sw.to_tg_id:
        await callback.answer(
            "«Поменяться» может нажать только тот, кому предложили обмен.",
            show_alert=True,
        )
        return
    q = await queries_db.get_queue_by_chat_message(
        db, sw.chat_id, sw.queue_message_id, for_update=True
    )
    if not q or q.status != qc.QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        await callback.answer("Очередь недоступна.", show_alert=True)
        return
    order = list(q.participants or [])
    if sw.from_tg_id not in order or sw.to_tg_id not in order:
        await callback.answer("Участники не в списке.", show_alert=True)
        return
    ia = max(i for i, x in enumerate(order) if x == sw.from_tg_id)
    ib = max(i for i, x in enumerate(order) if x == sw.to_tg_id)
    order[ia], order[ib] = order[ib], order[ia]
    q.participants = order
    flag_modified(q, "participants")
    await queries_db.sync_last_history_positions_after_swap(
        db,
        subject_id=q.subject_id,
        first_tg_id=sw.from_tg_id,
        first_new_pos_1based=ib + 1,
        second_tg_id=sw.to_tg_id,
        second_new_pos_1based=ia + 1,
        commit=False,
    )
    await queries_db.mark_swap_done(db, sw)
    cht = sw.chat_id
    qmid = sw.queue_message_id
    smid = sw.swap_message_id
    if smid:
        try:
            await bot.delete_message(cht, smid)
        except TelegramAPIError as exc:
            logger.debug("Не удалось удалить сообщение обмена: %s", exc)
    if cht is not None and qmid is not None:
        q2 = await queries_db.get_queue_by_chat_message(
            db, cht, qmid, for_update=True
        )
        if q2:
            await qc.refresh_queue_message(bot, db, q2, None)
    await callback.answer("Места поменяны ✅")
