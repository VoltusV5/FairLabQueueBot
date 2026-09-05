"""Репозиторий для работы с предметами (Subject)."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db.init_db import Subject

logger = logging.getLogger(__name__)


async def list_subject_names_for_chat(
    db: AsyncSession, chat_id: int
) -> list[str]:
    """Возвращает список названий предметов для чата.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.

    Returns:
        list[str]: Список названий предметов.
    """
    stmt = (
        select(Subject.subject_name)
        .where(Subject.chat_id == chat_id)
        .order_by(func.lower(Subject.subject_name))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_or_create_subject(
    db: AsyncSession, chat_id: int, subject_name: str
) -> Subject:
    """Возвращает существующий предмет или создает новый.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        subject_name: Название предмета.

    Returns:
        Subject: Объект предмета.
    """
    clean_name = subject_name.strip()
    stmt = select(Subject).where(
        Subject.chat_id == chat_id,
        Subject.subject_name == clean_name,
    )
    result = await db.execute(stmt)
    existing_subject = result.scalar_one_or_none()

    if existing_subject:
        return existing_subject

    new_subject = Subject(chat_id=chat_id, subject_name=clean_name)
    try:
        async with db.begin_nested():
            db.add(new_subject)
            await db.flush()
    except IntegrityError:
        logger.info(
            "Параллельное создание предмета '%s' в чате %d, читаем повторно.",
            clean_name,
            chat_id,
        )
        result = await db.execute(stmt)
        race_subject = result.scalar_one_or_none()
        if race_subject is None:
            raise
        return race_subject

    logger.debug(
        "Создан предмет '%s' (ID %d) в чате %d.",
        clean_name,
        new_subject.id,
        chat_id,
    )
    return new_subject


async def get_subject_by_id(
    db: AsyncSession, subject_id: int
) -> Subject | None:
    """Возвращает предмет по ID.

    Args:
        db: Асинхронная сессия БД.
        subject_id: ID предмета.

    Returns:
        Subject | None: Объект предмета, если найден.
    """
    return await db.get(Subject, subject_id)


async def get_subject_by_name(
    db: AsyncSession, chat_id: int, subject_name: str
) -> Subject | None:
    """Возвращает предмет по названию и chat_id.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        subject_name: Название предмета.

    Returns:
        Subject | None: Объект предмета, если найден.
    """
    clean_name = subject_name.strip()
    stmt = select(Subject).where(
        Subject.chat_id == chat_id,
        Subject.subject_name == clean_name,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def add_subject_king(
    db: AsyncSession, subject: Subject, tg_id: int
) -> bool:
    """Добавляет пользователя в список королей предмета.

    Args:
        db: Асинхронная сессия БД.
        subject: Объект предмета.
        tg_id: Telegram ID пользователя.

    Returns:
        bool: True, если добавлен, False если уже был королем.
    """
    kings = list(subject.kings or [])
    if tg_id in kings:
        return False
    kings.append(tg_id)
    subject.kings = kings
    flag_modified(subject, "kings")
    await db.flush()
    return True


async def remove_subject_king(
    db: AsyncSession, subject: Subject, tg_id: int
) -> bool:
    """Удаляет пользователя из списка королей предмета.

    Args:
        db: Асинхронная сессия БД.
        subject: Объект предмета.
        tg_id: Telegram ID пользователя.

    Returns:
        bool: True, если удален, False если не являлся королем.
    """
    kings = list(subject.kings or [])
    if tg_id not in kings:
        return False
    kings.remove(tg_id)
    subject.kings = kings
    flag_modified(subject, "kings")
    await db.flush()
    return True
