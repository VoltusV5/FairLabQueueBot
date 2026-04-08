"""Личка: старт, статистика, меню «на паре»; в группах: /attendance."""

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


def _presence_text(here_ids: list[int], db) -> str:
    lines = ["👥 Кто сейчас на паре?", ""]
    if not here_ids:
        lines.append("Пока никого.")
    else:
        for i, tid in enumerate(here_ids, 1):
            lines.append(f"{i}. {Q.get_user_display(db, tid)}")
    return "\n".join(lines)


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
        "В групповом чате отправьте команду /attendance — "
        "бот опубликует опрос «Кто сейчас на паре?»."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(LEXICON_RU["/help"])


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
async def cmd_attendance(message: Message, bot: Bot) -> None:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("Команда /attendance только в группе.")
        return
    cid = message.chat.id
    with get_db() as db:
        Q.ensure_chat(db, cid, message.chat.title)
        empty = _presence_text([], db)
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
        text = _presence_text(here, db)
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
