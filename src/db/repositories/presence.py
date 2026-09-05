"""Репозиторий для работы с опросами присутствия (PresencePoll)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db.init_db import PresencePoll

logger = logging.getLogger(__name__)


def _modify_presence_list(here_list: list[int], tg_id: int, add: bool) -> bool:
    """Добавляет или удаляет ID пользователя в списке присутствия."""
    if add:
        if tg_id not in here_list:
            here_list.append(tg_id)
            return True
    else:
        if tg_id in here_list:
            here_list.remove(tg_id)
            return True
    return False


async def get_presence_poll(
    db: AsyncSession,
    chat_id: int,
    message_id: int,
    *,
    for_update: bool = False,
) -> PresencePoll | None:
    """Возвращает опрос присутствия по чату и сообщению.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        message_id: ID сообщения опроса.
        for_update: Блокировать ли строку для обновления (FOR UPDATE).

    Returns:
        PresencePoll | None: Объект опроса, если найден.
    """
    stmt = select(PresencePoll).where(
        PresencePoll.chat_id == chat_id,
        PresencePoll.message_id == message_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_presence_poll(
    db: AsyncSession,
    chat_id: int,
    message_id: int,
    *,
    flush: bool = True,
) -> PresencePoll:
    """Создает опрос присутствия.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        message_id: ID сообщения.
        flush: Выполнять ли flush.

    Returns:
        PresencePoll: Созданный опрос.
    """
    poll = PresencePoll(chat_id=chat_id, message_id=message_id, here_tg_ids=[])
    db.add(poll)
    if flush:
        await db.flush()
    return poll


async def upsert_presence_here(
    db: AsyncSession, chat_id: int, message_id: int, tg_id: int, add: bool
) -> bool:
    """Обновляет присутствие пользователя на паре.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        message_id: ID сообщения.
        tg_id: Telegram ID пользователя.
        add: True — добавить, False — убрать.

    Returns:
        bool: True, если состояние изменилось, иначе False.
    """
    row = await get_presence_poll(db, chat_id, message_id, for_update=True)

    if row is None:
        try:
            async with db.begin_nested():
                row = PresencePoll(
                    chat_id=chat_id,
                    message_id=message_id,
                    here_tg_ids=[],
                )
                db.add(row)
                await db.flush()
        except IntegrityError:
            row = await get_presence_poll(
                db, chat_id, message_id, for_update=True
            )
            if row is None:
                raise

    here_list = list(row.here_tg_ids or [])
    if not _modify_presence_list(here_list, tg_id, add):
        return False

    row.here_tg_ids = here_list
    flag_modified(row, "here_tg_ids")
    await db.flush()
    return True
