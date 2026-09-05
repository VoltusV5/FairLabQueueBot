"""Репозиторий для работы с пользователями."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import User

logger = logging.getLogger(__name__)


def _normalize_username(username: str | None) -> str | None:
    """Приводит tg_username к нижнему регистру."""
    if username is None:
        return None
    normalized = username.strip().lstrip("@").lower()
    return normalized if normalized else None


def _build_upsert_values(
    tg_id: int,
    tg_username: str | None,
    real_name: str | None,
) -> dict[str, Any]:
    """Возвращает dict values для INSERT."""
    return {
        "tg_id": tg_id,
        "tg_username": tg_username,
        "real_name": real_name,
    }


def _build_upsert_set(
    tg_username: str | None,
    real_name: str | None,
) -> dict[str, Any]:
    """Возвращает словарь set_ для ON CONFLICT DO UPDATE.

    Использует COALESCE, чтобы не перезаписать существующие данные NULL-ом.
    """
    return {
        "tg_username": func.coalesce(tg_username, User.tg_username),
        "real_name": func.coalesce(real_name, User.real_name),
    }


async def _get_dialect_name(db: AsyncSession) -> str:
    """Безопасно возвращает имя диалекта активной сессии."""
    try:
        bind = db.get_bind()
        name: str = bind.dialect.name
        return name
    except Exception:
        return ""


# Тип для фабрик INSERT, совместимых с on_conflict_do_update
_InsertFactory = Callable[..., Any]


async def _execute_upsert(
    db: AsyncSession,
    insert_factory: _InsertFactory,
    tg_id: int,
    tg_username: str | None,
    real_name: str | None,
) -> User:
    """Выполняет операцию INSERT ... ON CONFLICT DO UPDATE RETURNING.

    Args:
        db: Асинхронная сессия БД.
        insert_factory: pg_insert или sqlite_insert.
        tg_id: Telegram ID пользователя.
        tg_username: Нормализованный юзернейм.
        real_name: Реальное имя пользователя.

    Returns:
        User: Объект пользователя из БД.
    """
    stmt = (
        insert_factory(User)
        .values(_build_upsert_values(tg_id, tg_username, real_name))
        .on_conflict_do_update(
            index_elements=["tg_id"],
            set_=_build_upsert_set(tg_username, real_name),
        )
        .returning(User)
    )
    res = await db.execute(stmt)
    await db.flush()
    user: User = res.scalar_one()
    return user


async def _update_user_fields(
    db: AsyncSession,
    user: User,
    tg_username: str | None,
    real_name: str | None,
) -> None:
    """Обновляет поля пользователя и сбрасывает изменения в БД."""
    changed = False
    if tg_username is not None and user.tg_username != tg_username:
        user.tg_username = tg_username
        changed = True
    if real_name is not None and user.real_name != real_name:
        user.real_name = real_name
        changed = True
    if changed:
        await db.flush()


async def ensure_user(
    db: AsyncSession,
    tg_id: int,
    tg_username: str | None,
    real_name: str | None,
) -> User:
    """Гарантирует существование пользователя в БД и обновляет его данные.

    Args:
        db: Асинхронная сессия БД.
        tg_id: Telegram ID пользователя.
        tg_username: Telegram username пользователя (без @).
        real_name: Реальное имя пользователя.

    Returns:
        User: Актуальный объект пользователя.
    """
    normalized_username = _normalize_username(tg_username)
    dialect_name = await _get_dialect_name(db)

    match dialect_name:
        case "postgresql":
            user = await _execute_upsert(
                db, pg_insert, tg_id, normalized_username, real_name
            )
            logger.debug(
                "Ensured user via PostgreSQL UPSERT tg_id=%s username=%s",
                tg_id,
                normalized_username,
            )
            return user

        case "sqlite":
            user = await _execute_upsert(
                db, sqlite_insert, tg_id, normalized_username, real_name
            )
            logger.debug(
                "Ensured user via SQLite UPSERT tg_id=%s username=%s",
                tg_id,
                normalized_username,
            )
            return user

        case _:
            found: User | None = await db.get(User, tg_id)
            if found is None:
                try:
                    async with db.begin_nested():
                        found = User(
                            tg_id=tg_id,
                            tg_username=normalized_username,
                            real_name=real_name,
                        )
                        db.add(found)
                        await db.flush()
                except IntegrityError:
                    found = await db.get(User, tg_id)
                    if found is None:
                        raise

            assert found is not None
            await _update_user_fields(
                db, found, normalized_username, real_name
            )
            logger.debug(
                "Ensured user tg_id=%s username=%s",
                tg_id,
                normalized_username,
            )
            return found


async def get_user_display(db: AsyncSession, tg_id: int) -> str:
    """Возвращает отображаемое имя пользователя.

    Args:
        db: Асинхронная сессия БД.
        tg_id: Telegram ID пользователя.

    Returns:
        str: Отображаемое имя.
    """
    user = await db.get(User, tg_id)
    if user is None:
        return str(tg_id)
    return user.display_name


async def get_users_display_map(
    db: AsyncSession, tg_ids: Sequence[int | str]
) -> dict[int | str, str]:
    """Возвращает отображаемые имена для списка пользователей.

    Args:
        db: Асинхронная сессия БД.
        tg_ids: Последовательность идентификаторов участников.

    Returns:
        dict[int | str, str]: Отображение ID -> отображаемое имя.
    """
    if not tg_ids:
        return {}
    real_ids = [int(uid) for uid in tg_ids if isinstance(uid, int) and uid > 0]
    result_map: dict[int | str, str] = {uid: str(uid) for uid in tg_ids}
    if real_ids:
        stmt = select(User).where(User.tg_id.in_(real_ids))
        users = (await db.execute(stmt)).scalars().all()
        for u in users:
            result_map[u.tg_id] = u.display_name
    return result_map


async def change_realname_for_user(
    db: AsyncSession, tg_id: int, new_name: str
) -> None:
    """Изменяет реальное имя пользователя.

    Args:
        db: Асинхронная сессия БД.
        tg_id: Telegram ID пользователя.
        new_name: Новое реальное имя.

    Raises:
        ValueError: Если пользователь не найден.
    """
    exists_stmt = select(User.tg_id).where(User.tg_id == tg_id)
    exists_result = await db.execute(exists_stmt)
    if exists_result.scalar_one_or_none() is None:
        raise ValueError("Пользователь не найден")
    stmt = update(User).where(User.tg_id == tg_id).values(real_name=new_name)
    await db.execute(stmt)
    await db.flush()
    logger.debug("Changed real_name for tg_id=%s to '%s'", tg_id, new_name)


async def find_user_by_username(
    db: AsyncSession, username: str
) -> User | None:
    """Ищет пользователя по username.

    Args:
        db: Асинхронная сессия БД.
        username: Имя пользователя в Telegram (без @ или с @).

    Returns:
        User | None: Объект пользователя, если найден, иначе None.
    """
    normalized_username = _normalize_username(username)
    stmt = select(User).where(User.tg_username == normalized_username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        logger.debug(
            "Found user tg_id=%s by username '%s'", user.tg_id, username
        )
    return user
