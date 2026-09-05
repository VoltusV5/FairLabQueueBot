"""Репозиторий для работы с очередями (Queue)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, TypedDict

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db.init_db import Queue, QueueStatus, SubmissionAttempt
from src.db.repositories.submission import (
    append_one_history_position,
    apply_slot_penalties_after_last_submitter,
    get_entry_pos,
    get_entry_status,
    shift_last_history_positions_after_insert,
)

logger = logging.getLogger(__name__)


class QueueExtra(TypedDict, total=False):
    """Типизированный словарь дополнительных метаданных очереди."""

    successful_slot_index: int
    pardoned_tg_ids: list[int]
    temp_names: dict[str, str]


class QueueError(Exception):
    """Базовое исключение для ошибок очереди."""


class QueueStatusError(QueueError):
    """Очередь находится в неверном статусе для выполнения операции."""


class ParticipantNotFoundError(QueueError):
    """Участник не найден в очереди."""


class ParticipantAlreadyExistsError(QueueError):
    """Участник уже находится в очереди."""


async def is_queue_duplicate(
    db: AsyncSession, subject_id: int, chat_id: int, lesson_date: datetime
) -> bool:
    """Проверяет, существует ли очередь для предмета на данную дату.

    Args:
        db: Асинхронная сессия БД.
        subject_id: ID предмета.
        chat_id: Telegram ID чата.
        lesson_date: Дата занятия.

    Returns:
        bool: True, если очередь существует, иначе False.
    """
    stmt = select(
        exists().where(
            Queue.subject_id == subject_id,
            Queue.chat_id == chat_id,
            Queue.lesson_date == lesson_date,
        )
    )
    result = await db.execute(stmt)
    return bool(result.scalar())


async def add_queue_row(
    db: AsyncSession,
    *,
    subject_id: int,
    chat_id: int,
    message_id: int,
    lesson_date: datetime,
    close_at: datetime | None,
    status: QueueStatus | str = QueueStatus.WAITING_FOR_PARTICIPANTS,
    participants: list[int],
    extra: dict[str, Any] | None = None,
) -> Queue:
    """Создает запись об очереди.

    Args:
        db: Асинхронная сессия БД.
        subject_id: ID предмета.
        chat_id: Telegram ID чата.
        message_id: ID сообщения.
        lesson_date: Дата занятия.
        close_at: Время закрытия набора.
        status: Статус очереди.
        participants: Список участников (Telegram ID).
        extra: Дополнительные данные.

    Returns:
        Queue: Объект созданной очереди.
    """
    row = Queue(
        subject_id=subject_id,
        chat_id=chat_id,
        message_id=message_id,
        lesson_date=lesson_date,
        close_at=close_at,
        status=status,
        participants=list(participants),
        extra=extra or {},
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def get_queue_by_chat_message(
    db: AsyncSession, chat_id: int, message_id: int, for_update: bool = False
) -> Queue | None:
    """Ищет очередь по ID чата и ID сообщения.

    Args:
        db: Асинхронная сессия БД.
        chat_id: Telegram ID чата.
        message_id: ID сообщения.
        for_update: Блокировать ли строку для обновления.

    Returns:
        Queue | None: Объект очереди.
    """
    stmt = select(Queue).where(
        Queue.chat_id == chat_id, Queue.message_id == message_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def merge_extra(queue: Queue, patch: QueueExtra | dict[str, Any]) -> None:
    """Объединяет новые данные со словарем extra в очереди.

    Требует, чтобы объект queue был заблокирован в текущей транзакции.

    Args:
        queue: Объект очереди.
        patch: Словарь для обновления.
    """
    current_extra = dict(queue.extra or {})
    current_extra.update(patch)
    queue.extra = current_extra
    flag_modified(queue, "extra")


async def complete_queue_last_submitter(
    db: AsyncSession, queue: Queue, last_submitter_tg_id: int
) -> None:
    """Завершает очередь: последний сдавший получает статус. Выдаём штрафы.

    Требует, чтобы объект queue был предварительно загружен с for_update=True.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
        last_submitter_tg_id: Telegram ID последнего сдавшего.

    Raises:
        ParticipantNotFoundError: Если пользователь не найден в очереди.
        QueueStatusError: Если статус очереди не подходит.
    """
    if queue.status != QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        raise QueueStatusError(
            "Очередь еще не сформирована или уже завершена."
        )

    participants_order = list(queue.participants or [])

    matching_indices = [
        index
        for index, participant_id in enumerate(participants_order)
        if participant_id == last_submitter_tg_id
    ]
    if not matching_indices:
        raise ParticipantNotFoundError("Пользователь не в очереди.")
    last_submitter_index = matching_indices[-1]

    merge_extra(queue, {"successful_slot_index": last_submitter_index})
    queue_extra = queue.extra or {}
    pardoned_tg_ids = queue_extra.get("pardoned_tg_ids", [])

    await apply_slot_penalties_after_last_submitter(
        db,
        participants_order,
        last_submitter_index,
        queue.subject_id,
        pardoned_tg_ids,
    )
    queue.status = QueueStatus.COMPLETED


async def insert_into_formed_queue(
    db: AsyncSession, queue: Queue, tg_id: int, pos_1based: int
) -> None:
    """Вставляет участника в сформированную очередь.

    Требует, чтобы объект queue был предварительно загружен с for_update=True.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
        tg_id: Telegram ID участника.
        pos_1based: Позиция для вставки (начиная с 1).

    Raises:
        QueueStatusError: Если очередь не сформирована.
        ParticipantAlreadyExistsError: Если пользователь уже в списке.
        ValueError: Если неверное место.
    """
    if queue.status != QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        raise QueueStatusError("Нужна сформированная очередь.")

    participants_order = list(queue.participants or [])

    if tg_id in participants_order:
        raise ParticipantAlreadyExistsError("Уже в списке.")

    total_participants = len(participants_order)
    if pos_1based < 1 or pos_1based > total_participants + 1:
        raise ValueError("Неверный номер места.")

    shifted_pairs = [
        (participant_id, idx + 2)
        for idx, participant_id in enumerate(
            participants_order[pos_1based - 1 :], start=pos_1based - 1
        )
        if isinstance(participant_id, int) and participant_id > 0
    ]

    participants_order.insert(pos_1based - 1, tg_id)
    queue.participants = participants_order
    flag_modified(queue, "participants")
    await append_one_history_position(
        db, tg_id, queue.subject_id, str(pos_1based)
    )

    if shifted_pairs:
        await shift_last_history_positions_after_insert(
            db,
            subject_id=queue.subject_id,
            shifted_participants_with_new_pos=shifted_pairs,
        )


async def add_participant(db: AsyncSession, queue: Queue, tg_id: int) -> None:
    """Добавляет участника в очередь.

    Требует, чтобы объект queue был предварительно загружен с for_update=True.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
        tg_id: Telegram ID участника.

    Raises:
        ParticipantAlreadyExistsError: Если участник уже в списке.
    """
    participant_ids = list(queue.participants or [])
    if tg_id in participant_ids:
        raise ParticipantAlreadyExistsError("Участник уже в очереди.")
    participant_ids.append(tg_id)
    queue.participants = participant_ids
    flag_modified(queue, "participants")


async def remove_participant(
    db: AsyncSession, queue: Queue, tg_id: int
) -> bool:
    """Удаляет участника из очереди.

    Требует, чтобы объект queue был предварительно загружен с for_update=True.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
        tg_id: Telegram ID участника.

    Returns:
        bool: True, если список изменился, иначе False.
    """
    participant_ids = list(queue.participants or [])
    if tg_id not in participant_ids:
        return False
    participant_ids.remove(tg_id)
    queue.participants = participant_ids
    flag_modified(queue, "participants")
    return True


async def delete_queue_row(db: AsyncSession, queue: Queue) -> None:
    """Удаляет очередь.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
    """
    await db.delete(queue)


async def list_queues_waiting_last(db: AsyncSession) -> Sequence[Queue]:
    """Возвращает список очередей, ожидающих последнего участника.

    Args:
        db: Асинхронная сессия БД.

    Returns:
        Sequence[Queue]: Список очередей.
    """
    stmt = select(Queue).where(
        Queue.status == QueueStatus.WAITING_FOR_LAST_PARTICIPANT
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def list_queues_recruiting(db: AsyncSession) -> Sequence[Queue]:
    """Возвращает список очередей на этапе набора участников.

    Args:
        db: Асинхронная сессия БД.

    Returns:
        Sequence[Queue]: Список очередей.
    """
    stmt = select(Queue).where(
        Queue.status == QueueStatus.WAITING_FOR_PARTICIPANTS
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def pardon_queue_participant(
    db: AsyncSession, queue: Queue, tg_id: int
) -> tuple[bool, str]:
    """Помилование участника очереди старостой.

    Если очередь в статусе WAITING_FOR_LAST_PARTICIPANT — добавляет в список
    помилованных (получит пропуск при закрытии).
    Если очередь COMPLETED — отменяет успешную сдачу задним числом, добавляет
    +1 к штрафным (missed) попыткам и меняет запись в истории.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
        tg_id: Telegram ID участника.

    Returns:
        tuple[bool, str]: (Успех операции, строковый код результата).
    """
    if queue.status == QueueStatus.WAITING_FOR_LAST_PARTICIPANT:
        extra_data = queue.extra or {}
        pardoned_ids = set(extra_data.get("pardoned_tg_ids", []))
        pardoned_ids.add(tg_id)
        merge_extra(queue, {"pardoned_tg_ids": list(pardoned_ids)})
        await db.flush()
        return True, "pardoned_in_advance"

    if queue.status == QueueStatus.COMPLETED:
        extra_data = queue.extra or {}
        completed_pardoned = set(extra_data.get("pardoned_tg_ids", []))
        if tg_id in completed_pardoned:
            return False, "already_pardoned"

        participants_order = list(queue.participants or [])
        user_idx = -1
        for idx, p_id in enumerate(participants_order):
            if p_id == tg_id:
                user_idx = idx

        if user_idx == -1:
            return False, "not_in_queue"

        successful_slot_index = extra_data.get("successful_slot_index", -1)
        if user_idx > successful_slot_index and successful_slot_index != -1:
            return False, "already_missed"

        stmt = select(SubmissionAttempt).where(
            SubmissionAttempt.tg_id == tg_id,
            SubmissionAttempt.subject_id == queue.subject_id,
        )
        res = await db.execute(stmt)
        sub_row = res.scalar_one_or_none()

        if not sub_row or not sub_row.history_position:
            return False, "no_history"

        target_pos = user_idx + 1
        hp = list(sub_row.history_position)
        last_idx = -1
        for i in range(len(hp) - 1, -1, -1):
            if (
                get_entry_pos(hp[i]) == target_pos
                and get_entry_status(hp[i]) == "submitted"
            ):
                last_idx = i
                break

        if last_idx != -1:
            hp[last_idx] = {"pos": target_pos, "status": "missed"}
            sub_row.history_position = hp
            sub_row.missed_attempts_count = (
                int(sub_row.missed_attempts_count or 0) + 1
            )
            flag_modified(sub_row, "history_position")

            completed_pardoned.add(tg_id)
            merge_extra(queue, {"pardoned_tg_ids": list(completed_pardoned)})
            await db.flush()
            return True, "retroactive_pardon"

        return False, "entry_not_found"

    return False, "invalid_status"


async def rollback_and_delete_queue(db: AsyncSession, queue: Queue) -> None:
    """Удаляет очередь со сбросом и откатом истории попыток сдачи участников.

    Args:
        db: Асинхронная сессия БД.
        queue: Объект очереди.
    """
    if queue.status == QueueStatus.WAITING_FOR_PARTICIPANTS:
        await db.delete(queue)
        await db.flush()
        return

    extra_data = queue.extra or {}
    order = list(queue.participants or [])
    pardoned = set(extra_data.get("pardoned_tg_ids", []))
    successful_slot_index = extra_data.get("successful_slot_index", -1)

    uids = [entry for entry in order if isinstance(entry, int)]
    if uids:
        stmt = select(SubmissionAttempt).where(
            SubmissionAttempt.tg_id.in_(uids),
            SubmissionAttempt.subject_id == queue.subject_id,
        )
        res = await db.execute(stmt)
        rows_by_uid = {r.tg_id: r for r in res.scalars().all()}

        for idx, entry in enumerate(order):
            if not isinstance(entry, int):
                continue
            sub_row = rows_by_uid.get(entry)
            if not sub_row or not sub_row.history_position:
                continue

            hp = list(sub_row.history_position)
            target_pos = idx + 1
            if (
                queue.status == QueueStatus.COMPLETED
                and idx <= successful_slot_index
                and entry not in pardoned
            ):
                expected_status = "submitted"
            elif queue.status != QueueStatus.COMPLETED:
                expected_status = "submitted"
            else:
                expected_status = "missed"

            last_idx = -1
            found_status = None
            for i in range(len(hp) - 1, -1, -1):
                if (
                    get_entry_pos(hp[i]) == target_pos
                    and get_entry_status(hp[i]) == expected_status
                ):
                    last_idx = i
                    found_status = expected_status
                    break

            if last_idx == -1:
                other_status = (
                    "missed" if expected_status == "submitted" else "submitted"
                )
                for i in range(len(hp) - 1, -1, -1):
                    if (
                        get_entry_pos(hp[i]) == target_pos
                        and get_entry_status(hp[i]) == other_status
                    ):
                        last_idx = i
                        found_status = other_status
                        break

            if last_idx != -1:
                hp.pop(last_idx)
                sub_row.history_position = hp
                flag_modified(sub_row, "history_position")
                if (
                    found_status == "missed"
                    and queue.status == QueueStatus.COMPLETED
                ):
                    sub_row.missed_attempts_count = max(
                        0, int(sub_row.missed_attempts_count or 0) - 1
                    )

    await db.delete(queue)
    await db.flush()
