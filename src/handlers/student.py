"""Обработчики студенческих команд и администрирования групп.

Включает:
- Личные сообщения: /start, /help, /stat, меню «на паре».
- Групповые опросы присутствия: /who и inline-кнопки отметки.
- Просмотр предметов группы: /subjects.
- Управление старостами: /set_starosta, /rm_starosta.
- Управление королями предметов: /king, /unking.
- Модерация очередей старостой: /confirm (помилование), /rm (удаление).
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
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
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.handlers.queue_common import subject_from_formed
from src.lexicon import LEXICON_RU
from src.services.statistics_export import (
    build_user_stats_text,
    stats_file_bytes,
)
from src.services.subscription import effective_access, has_base_features

logger = logging.getLogger(__name__)

router = Router()

MENU_ATTENDANCE = "📍 Отметиться на паре"


def _attendance_kb(chat_id: int, message_id: int) -> InlineKeyboardMarkup:
    """Формирует inline-клавиатуру для опроса присутствия на паре.

    Args:
        chat_id: Telegram ID чата группы.
        message_id: ID сообщения с опросом.

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками «На паре» / «Не на паре».
    """
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


async def _format_leaderboard(
    db: AsyncSession, here_ids: list[int], subject_name: str | None = None
) -> str:
    """Форматирует текст списка присутствующих студентов на занятии.

    Args:
        db: Асинхронная сессия БД.
        here_ids: Список Telegram ID присутствующих.
        subject_name: Название предмета (если указано).

    Returns:
        str: Отформатированный список студентов.
    """
    if subject_name:
        lines = [f"👥 Кто сейчас на паре по {subject_name}?", ""]
    else:
        lines = ["👥 Кто сейчас на паре?", ""]

    if not here_ids:
        lines.append("Пока никого.")
    else:
        display_map = await queries_db.get_users_display_map(db, here_ids)
        for i, tid in enumerate(here_ids, 1):
            disp = display_map.get(tid, str(tid))
            lines.append(f"{i}. {disp}")
    return "\n".join(lines)


def _extract_presence_subject(text: str | None) -> str | None:
    """Извлекает название предмета из заголовка сообщения опроса присутствия.

    Args:
        text: Текст сообщения.

    Returns:
        str | None: Название предмета или None.
    """
    if not text:
        return None
    raw_lines = text.splitlines()
    first = raw_lines[0].strip() if raw_lines else ""
    pref = "👥 Кто сейчас на паре по "
    if first.startswith(pref) and first.endswith("?"):
        subj = first[len(pref) : -1].strip()
        return subj or None
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    """Обрабатывает команду /start.

    Приветствует пользователя, проверяет статус подписки чата и
    в личных сообщениях отправляет меню с быстрыми действиями.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    db = session
    chat = await queries_db.ensure_chat(
        db, message.chat.id, message.chat.title
    )
    acc = effective_access(chat)
    is_active = has_base_features(acc)

    sub_status = "активна ✅" if is_active else "не активна ❌"
    text = LEXICON_RU["/start"].format(
        sub_status=sub_status,
        chat_id=message.chat.id,
    )

    if message.chat.type == ChatType.PRIVATE:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=MENU_ATTENDANCE)]],
            resize_keyboard=True,
        )
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text)


@router.message(F.text == MENU_ATTENDANCE)
async def menu_attendance_hint(
    message: Message, session: AsyncSession
) -> None:
    """Отправляет подсказку по использованию отметки присутствия в группах.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type != ChatType.PRIVATE:
        return
    await message.answer(
        "В групповом чате отправьте команду /who — "
        "бот опубликует опрос «Кто сейчас на паре?»."
    )


def _help_kb(active_tier: str | None = None) -> InlineKeyboardMarkup:
    """Формирует инлайн-клавиатуру для переключения разделов справки /help.

    Args:
        active_tier: Текущий выбранный уровень ('base', 'supervip' или None).

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками Base и SuperVIP.
    """
    base_label = "📚 Base ✅" if active_tier == "base" else "📚 Base"
    supervip_label = (
        "👑 SuperVIP ✅" if active_tier == "supervip" else "👑 SuperVIP"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=base_label,
                    callback_data="help_tier|base",
                ),
                InlineKeyboardButton(
                    text=supervip_label,
                    callback_data="help_tier|supervip",
                ),
            ]
        ]
    )


@router.message(Command("help"))
async def cmd_help(message: Message, session: AsyncSession) -> None:
    """Выводит справочную информацию по доступным командам.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    await message.answer(
        LEXICON_RU["/help"],
        reply_markup=_help_kb(),
    )


@router.callback_query(F.data.startswith("help_tier|"))
async def cb_help_tier(callback: CallbackQuery) -> None:
    """Переключает текст справки в зависимости от выбранного уровня подписки.

    Args:
        callback: Входящий callback-запрос aiogram.
    """
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return

    tier = callback.data.split("|")[-1]
    if tier == "base":
        text = LEXICON_RU["/help_base"]
    elif tier == "supervip":
        text = LEXICON_RU["/help_supervip"]
    else:
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            text,
            reply_markup=_help_kb(tier),
        )
    except Exception as e:
        logger.debug("help_tier edit ignored: %s", e)

    await callback.answer()


@router.message(Command("subjects"))
async def cmd_subjects(message: Message, session: AsyncSession) -> None:
    """Выводит список зарегистрированных предметов для текущего чата.

    В личных сообщениях принимает опциональный ID целевого чата.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    parts = (message.text or "").split()
    target_chat_id = message.chat.id

    if message.chat.type == ChatType.PRIVATE:
        if len(parts) < 2:
            await message.answer(
                "В личке бота используйте: /subjects <id_чата>"
            )
            return
        try:
            target_chat_id = int(parts[1])
        except ValueError:
            await message.answer(
                "Некорректный id чата. Пример: /subjects -1001234567890"
            )
            return

    db = session
    await queries_db.ensure_chat(db, target_chat_id, message.chat.title)
    subjects = await queries_db.list_subject_names_for_chat(db, target_chat_id)
    if not subjects:
        await message.answer("Для этого чата пока нет созданных предметов.")
        return
    lines = [f"📚 Предметы чата {target_chat_id}" + ":"]
    for i, name in enumerate(subjects, 1):
        lines.append(f"{i}. {name}")
    await message.answer("\n".join(lines))


@router.message(Command("stat"))
async def cmd_stat(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Генерирует и отправляет текстовый файл со статистикой пользователя.

    Доступно только в личных сообщениях с ботом во избежание спама в группах.

    Args:
        message: Входящее сообщение aiogram.
        bot: Экземпляр бота aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type != ChatType.PRIVATE:
        await message.answer(
            "Запросите статистику в личных сообщениях с ботом: /stat"
        )
        return

    parts = (message.text or "").split()
    target_id = message.from_user.id if message.from_user else 0
    db = session

    if len(parts) >= 2:
        if parts[1].isdigit():
            target_id = int(parts[1])
        elif parts[1].startswith("@"):
            username = parts[1][1:].strip()
            user = await queries_db.find_user_by_username(db, username)
            if user:
                target_id = user.tg_id
            else:
                await message.answer(
                    "Пользователь с таким @ не найден в базе."
                )
                return

    text = await build_user_stats_text(db, target_id)
    bio = stats_file_bytes(target_id, text)
    await bot.send_document(
        message.chat.id,
        BufferedInputFile(bio.getvalue(), filename=f"stat_{target_id}.txt"),
        caption=f"Статистика (tg_id={target_id})",
    )


@router.message(Command("attendance"))
async def cmd_attendance_obsolete(
    message: Message, session: AsyncSession
) -> None:
    """Уведомляет об устаревании команды /attendance в пользу /who.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    await message.answer("Команда /attendance была заменена на /who.")


@router.message(Command("who"))
async def cmd_who(message: Message, bot: Bot, session: AsyncSession) -> None:
    """Публикует в группе опрос присутствия на текущей паре.

    Args:
        message: Входящее сообщение aiogram.
        bot: Экземпляр бота aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("Команда /who только в группе.")
        return

    cid = message.chat.id
    tokens = (message.text or "").split(maxsplit=1)
    subject_name = tokens[1].strip() if len(tokens) > 1 else None
    db = session

    await queries_db.ensure_chat(db, cid, message.chat.title)
    empty = await _format_leaderboard(db, [], subject_name)
    sent = await message.answer(empty)
    mid = sent.message_id
    await bot.edit_message_reply_markup(
        chat_id=cid,
        message_id=mid,
        reply_markup=_attendance_kb(cid, mid),
    )


@router.callback_query(F.data.startswith("ph|"))
async def cb_presence(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    """Обрабатывает нажатие кнопок «На паре» / «Не на паре».

    Args:
        callback: Входящий callback-запрос aiogram.
        bot: Экземпляр бота aiogram.
        session: Асинхронная сессия БД.
    """
    if not callback.data:
        return
    parts = callback.data.split("|")
    if len(parts) != 4:
        return

    _, cid_s, mid_s, mode = parts
    cid, mid = int(cid_s), int(mid_s)
    add = mode == "1"
    tg_id = callback.from_user.id
    un = callback.from_user.username
    rn = callback.from_user.full_name
    db = session

    await queries_db.ensure_user(db, tg_id, un, rn)
    changed = await queries_db.upsert_presence_here(db, cid, mid, tg_id, add)
    row = await queries_db.get_presence_poll(db, cid, mid)
    here = list(row.here_tg_ids if row else [])

    msg_text = (
        callback.message.text
        if isinstance(callback.message, Message)
        else None
    )
    subj = _extract_presence_subject(msg_text)
    text = await _format_leaderboard(db, here, subj)

    if not changed:
        await callback.answer(
            "Уже учтено." if add else "Вас не было в списке."
        )
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
async def cmd_set_starosta(message: Message, session: AsyncSession) -> None:
    """Назначает пользователя старостой (администратором) текущей группы.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].startswith("@"):
        await message.answer("Формат: /set_starosta @username")
        return
    username = args[1][1:].strip()
    db = session

    user = await queries_db.find_user_by_username(db, username)
    if not user:
        await message.answer(
            f"Пользователь @{username} не найден в базе бота. "
            "Попросите его нажать /start в личных сообщениях с ботом."
        )
        return

    added = await queries_db.add_chat_admin(db, message.chat.id, user.tg_id)
    if added:
        await message.answer(
            f"Пользователь @{username} назначен старостой группы."
        )
    else:
        await message.answer(
            f"Пользователь @{username} уже является старостой."
        )


@router.message(Command("rm_starosta"))
async def cmd_rm_starosta(message: Message, session: AsyncSession) -> None:
    """Снимает с пользователя права старосты (администратора) текущей группы.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].startswith("@"):
        await message.answer("Формат: /rm_starosta @username")
        return
    username = args[1][1:].strip()
    db = session

    user = await queries_db.find_user_by_username(db, username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден в базе.")
        return

    removed = await queries_db.remove_chat_admin(
        db, message.chat.id, user.tg_id
    )
    if removed:
        await message.answer(
            f"Пользователь @{username} удалён из списка старост."
        )
    else:
        await message.answer("Этот пользователь не является старостой.")


@router.message(Command("confirm"))
async def cmd_confirm(message: Message, session: AsyncSession) -> None:
    """Помилование участника очереди старостой.

    Применяется ответом на сообщение сформированной или закрытой очереди.
    Отменяет штраф или назначает пропуск вместо штрафного подхода.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return

    if not message.reply_to_message:
        await message.answer("Ответьте этой командой на сообщение с очередью.")
        return

    reply_text = message.reply_to_message.text
    if not reply_text:
        await message.answer(
            "Ответьте на сообщение со сформированной очередью "
            "(где есть текст «Список на...»)."
        )
        return

    subj_name = subject_from_formed(reply_text)
    if not subj_name:
        await message.answer(
            "Ответьте на сообщение со сформированной очередью "
            "(где есть текст «Список на...»)."
        )
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].startswith("@"):
        await message.answer("Формат: /confirm @username")
        return
    username = args[1][1:].strip()
    db = session

    chat = await queries_db.ensure_chat(
        db, message.chat.id, message.chat.title
    )
    admins = chat.admins or []
    if not admins:
        await message.answer(
            "В этой группе не назначен староста. "
            "Назначьте его командой /set_starosta @username"
        )
        return

    sender_id = message.from_user.id if message.from_user else 0
    if sender_id not in admins:
        await message.answer("Только староста может использовать эту команду.")
        return

    user = await queries_db.find_user_by_username(db, username)
    if not user:
        await message.answer(
            f"Пользователь @{username} не найден в базе бота."
        )
        return

    subj = await queries_db.get_subject_by_name(db, message.chat.id, subj_name)
    if not subj:
        await message.answer("Предмет не найден в этой группе.")
        return

    q = await queries_db.get_queue_by_chat_message(
        db,
        message.chat.id,
        message.reply_to_message.message_id,
        for_update=True,
    )
    if not q:
        await message.answer(
            "Очередь не найдена в базе (возможно, сообщение было удалено)."
        )
        return

    ok, code = await queries_db.pardon_queue_participant(db, q, user.tg_id)
    if ok:
        if code == "pardoned_in_advance":
            await message.answer(
                f"✅ Пользователь @{username} помилован. "
                "Когда очередь будет закрыта, он получит пропуск "
                "вместо штрафного подхода."
            )
        else:
            await message.answer(
                "✅ Успешный подход отменён задним числом. "
                f"@{username} получил +1 к пропущенным слотам."
            )
    else:
        error_messages = {
            "already_pardoned": "Этот пользователь уже был помилован.",
            "not_in_queue": "Пользователь не участвовал в этой очереди.",
            "already_missed": (
                "Пользователь и так находился после последнего сдававшего, "
                "поэтому уже получил пропуск."
            ),
            "no_history": (
                f"У пользователя @{username} нет истории по этому предмету."
            ),
            "entry_not_found": (
                "Не удалось найти соответствующую позицию в истории. "
                "Возможно, она уже была изменена."
            ),
            "invalid_status": (
                "Очередь находится в статусе, не подходящем для этой команды."
            ),
        }
        err_text = error_messages.get(
            code, "Не удалось выполнить помилование."
        )
        await message.answer(err_text)


@router.message(Command("king"))
async def cmd_king(message: Message, session: AsyncSession) -> None:
    """Назначает пользователя королем (бригадиром) по выбранному предмету.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3 or not args[2].startswith("@"):
        await message.answer("Формат: /king Название_предмета @username")
        return

    subj_name = args[1].strip()
    username = args[2][1:].strip()
    db = session

    user = await queries_db.find_user_by_username(db, username)
    if not user:
        await message.answer(
            f"Пользователь @{username} не найден в базе бота."
        )
        return

    subj = await queries_db.get_subject_by_name(db, message.chat.id, subj_name)
    if not subj:
        await message.answer(f"Предмет «{subj_name}» не найден в этой группе.")
        return

    added = await queries_db.add_subject_king(db, subj, user.tg_id)
    if added:
        await message.answer(
            f"👑 Пользователь @{username} назначен командиром бригады "
            f"по предмету «{subj_name}»."
        )
    else:
        await message.answer(
            f"Пользователь @{username} уже является королём по этому предмету."
        )


@router.message(Command("unking"))
async def cmd_unking(message: Message, session: AsyncSession) -> None:
    """Снимает с пользователя статус короля по выбранному предмету.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3 or not args[2].startswith("@"):
        await message.answer("Формат: /unking Название_предмета @username")
        return

    subj_name = args[1].strip()
    username = args[2][1:].strip()
    db = session

    user = await queries_db.find_user_by_username(db, username)
    if not user:
        await message.answer(
            f"Пользователь @{username} не найден в базе бота."
        )
        return

    subj = await queries_db.get_subject_by_name(db, message.chat.id, subj_name)
    if not subj:
        await message.answer(f"Предмет «{subj_name}» не найден в этой группе.")
        return

    removed = await queries_db.remove_subject_king(db, subj, user.tg_id)
    if removed:
        await message.answer(
            f"Пользователь @{username} лишён статуса короля "
            f"по предмету «{subj_name}»."
        )
    else:
        await message.answer(
            "Этот пользователь не является королём по этому предмету."
        )


@router.message(Command("rm"))
async def cmd_rm_queue(
    message: Message, bot: Bot, session: AsyncSession
) -> None:
    """Удаляет очередь старостой и откатывает статистику участников.

    Применяется ответом на сообщение с очередью.

    Args:
        message: Входящее сообщение aiogram.
        bot: Экземпляр бота aiogram.
        session: Асинхронная сессия БД.
    """
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Эта команда работает только в группах.")
        return

    if not message.reply_to_message:
        await message.answer("Ответьте этой командой на сообщение с очередью.")
        return

    db = session
    chat = await queries_db.ensure_chat(
        db, message.chat.id, message.chat.title
    )
    admins = chat.admins or []
    if not admins:
        await message.answer(
            "В этой группе не назначен староста. "
            "Назначить можно командой: /set_starosta @username"
        )
        return

    sender_id = message.from_user.id if message.from_user else 0
    if sender_id not in admins:
        await message.answer("Только староста может удалить очередь.")
        return

    reply_mid = message.reply_to_message.message_id
    q = await queries_db.get_queue_by_chat_message(
        db, message.chat.id, reply_mid, for_update=True
    )
    if not q:
        await message.answer("Очередь не найдена в БД.")
        return

    from_u = message.from_user
    user_label = (
        f"@{from_u.username}"
        if (from_u and from_u.username)
        else (from_u.first_name if from_u else "староста")
    )

    is_recruiting = q.status == "waiting_for_participants"
    q_chat_id = q.chat_id
    q_message_id = q.message_id

    await queries_db.rollback_and_delete_queue(db, q)

    if is_recruiting:
        try:
            await bot.edit_message_text(
                chat_id=q_chat_id,
                message_id=q_message_id,
                text=f"❌ Очередь отменена и удалена старостой {user_label}.",
                reply_markup=None,
            )
        except TelegramAPIError as exc:
            logger.debug("Не удалось обновить удаленную очередь: %s", exc)
        await message.answer(f"Очередь удалена {user_label}.")
    else:
        try:
            await bot.edit_message_text(
                chat_id=q_chat_id,
                message_id=q_message_id,
                text=(
                    f"❌ Очередь удалена старостой {user_label}. "
                    "Все записи в статистике отменены."
                ),
                reply_markup=None,
            )
        except TelegramAPIError as exc:
            logger.debug("Не удалось обновить удаленную очередь: %s", exc)
        await message.answer(
            f"Очередь удалена {user_label}, "
            "статистика участников восстановлена."
        )
