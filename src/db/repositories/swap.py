"""Репозиторий для работы с обменами (SwapRequest)."""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import SwapRequest, SwapStatus

logger = logging.getLogger(__name__)


async def _create_swap_request(
    db: AsyncSession,
    *,
    chat_id: int,
    queue_message_id: int,
    subject_id: int,
    from_tg_id: int,
    to_tg_id: int | None,
    status: SwapStatus,
    swap_message_id: int | None = None,
) -> SwapRequest:
    """Внутренний фабричный метод для создания заявки на обмен."""
    swap_request = SwapRequest(
        chat_id=chat_id,
        queue_message_id=queue_message_id,
        subject_id=subject_id,
        from_tg_id=from_tg_id,
        to_tg_id=to_tg_id,
        status=status.value,
        swap_message_id=swap_message_id,
    )
    db.add(swap_request)
    await db.flush()
    logger.debug(
        "Created SwapRequest id=%s status=%s from=%s to=%s queue_msg_id=%s",
        swap_request.id,
        status.value,
        from_tg_id,
        to_tg_id,
        queue_message_id,
    )
    return swap_request


async def open_swap(
    db: AsyncSession,
    chat_id: int,
    queue_message_id: int,
    subject_id: int,
    from_tg_id: int,
    swap_message_id: int,
) -> SwapRequest:
    """Создает открытую заявку на обмен.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        queue_message_id: ID сообщения очереди.
        subject_id: ID предмета.
        from_tg_id: Telegram ID инициатора.
        swap_message_id: ID сообщения обмена.

    Returns:
        SwapRequest: Созданная заявка на обмен.
    """
    return await _create_swap_request(
        db,
        chat_id=chat_id,
        queue_message_id=queue_message_id,
        subject_id=subject_id,
        from_tg_id=from_tg_id,
        to_tg_id=None,
        status=SwapStatus.OPEN,
        swap_message_id=swap_message_id,
    )


async def find_open_swap_for_message(
    db: AsyncSession, queue_message_id: int, *, for_update: bool = False
) -> SwapRequest | None:
    """Ищет открытую заявку для сообщения очереди.

    Второй участник ещё не нажал «Поменяться».

    Args:
        db: Асинхронная сессия БД.
        queue_message_id: ID сообщения очереди.
        for_update: Блокировать ли запись в БД FOR UPDATE.

    Returns:
        SwapRequest | None: Заявка на обмен, если найдена.
    """
    stmt = select(SwapRequest).where(
        SwapRequest.queue_message_id == queue_message_id,
        SwapRequest.status == SwapStatus.OPEN.value,
        SwapRequest.to_tg_id.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def delete_swaps_pending_for_queue(
    db: AsyncSession, queue_message_id: int
) -> None:
    """Удаляет незавершённые обмены по сообщению очереди.

    Args:
        db: Асинхронная сессия БД.
        queue_message_id: ID сообщения очереди.
    """
    stmt = delete(SwapRequest).where(
        SwapRequest.queue_message_id == queue_message_id,
        SwapRequest.status.in_(
            (SwapStatus.OPEN.value, SwapStatus.AWAIT_ACCEPT.value)
        ),
    )
    await db.execute(stmt)
    await db.flush()
    logger.debug(
        "Deleted pending swap requests for queue_message_id=%s",
        queue_message_id,
    )


async def get_swap_request(
    db: AsyncSession, swap_id: int, *, for_update: bool = False
) -> SwapRequest | None:
    """Возвращает заявку на обмен по ID.

    Args:
        db: Асинхронная сессия БД.
        swap_id: ID заявки на обмен.
        for_update: Блокировать ли запись в БД FOR UPDATE.

    Returns:
        SwapRequest | None: Заявка, если найдена.
    """
    if for_update:
        stmt = (
            select(SwapRequest)
            .where(SwapRequest.id == swap_id)
            .with_for_update()
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    return await db.get(SwapRequest, swap_id)


async def delete_swap_request_row(
    db: AsyncSession, swap_request: SwapRequest
) -> None:
    """Удаляет заявку на обмен.

    Args:
        db: Асинхронная сессия БД.
        swap_request: Заявка на обмен.
    """
    swap_id = swap_request.id
    await db.delete(swap_request)
    await db.flush()
    logger.debug("Deleted SwapRequest id=%s", swap_id)


async def create_formed_swap_request(
    db: AsyncSession,
    chat_id: int,
    queue_message_id: int,
    subject_id: int,
    from_tg_id: int,
    to_tg_id: int,
    confirm_message_id: int | None = None,
) -> SwapRequest:
    """Создает сформированную заявку на обмен (ожидает подтверждения).

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        queue_message_id: ID сообщения очереди.
        subject_id: ID предмета.
        from_tg_id: Telegram ID инициатора.
        to_tg_id: Telegram ID получателя.
        confirm_message_id: ID сообщения подтверждения.

    Returns:
        SwapRequest: Созданная заявка.
    """
    return await _create_swap_request(
        db,
        chat_id=chat_id,
        queue_message_id=queue_message_id,
        subject_id=subject_id,
        from_tg_id=from_tg_id,
        to_tg_id=to_tg_id,
        status=SwapStatus.AWAIT_ACCEPT,
        swap_message_id=confirm_message_id,
    )


async def set_swap_request_message_id(
    db: AsyncSession, swap_request: SwapRequest, msg_id: int
) -> None:
    """Обновляет ID сообщения для обмена.

    Args:
        db: Асинхронная сессия БД.
        swap_request: Заявка на обмен.
        msg_id: Новый ID сообщения.
    """
    swap_request.swap_message_id = msg_id
    await db.flush()
    logger.debug(
        "Updated SwapRequest id=%s swap_message_id=%s", swap_request.id, msg_id
    )


async def mark_swap_done(db: AsyncSession, swap_request: SwapRequest) -> None:
    """Помечает обмен как выполненный.

    Args:
        db: Асинхронная сессия БД.
        swap_request: Заявка на обмен.
    """
    swap_request.status = SwapStatus.DONE.value
    await db.flush()
    logger.debug("Marked SwapRequest id=%s as done", swap_request.id)


async def complete_swap(
    db: AsyncSession, swap_request: SwapRequest, to_tg_id: int
) -> None:
    """Завершает обмен (устанавливает второго участника и статус 'done').

    Args:
        db: Асинхронная сессия БД.
        swap_request: Заявка на обмен.
        to_tg_id: Telegram ID второго участника.
    """
    swap_request.to_tg_id = to_tg_id
    swap_request.status = SwapStatus.DONE.value
    await db.flush()
    logger.debug(
        "Completed SwapRequest id=%s for to_tg_id=%s",
        swap_request.id,
        to_tg_id,
    )


async def delete_swaps_for_queue(
    db: AsyncSession, queue_message_id: int
) -> None:
    """Удаляет все обмены для очереди.

    Args:
        db: Асинхронная сессия БД.
        queue_message_id: ID сообщения очереди.
    """
    stmt = delete(SwapRequest).where(
        SwapRequest.queue_message_id == queue_message_id
    )
    await db.execute(stmt)
    await db.flush()
    logger.debug(
        "Deleted all swap requests for queue_message_id=%s",
        queue_message_id,
    )
