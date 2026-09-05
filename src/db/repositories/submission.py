"""Репозиторий для работы с попытками сдачи (SubmissionAttempt)."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from src.db.init_db import SubmissionAttempt

logger = logging.getLogger(__name__)

type HistoryEntry = dict[str, Any] | str | int


def get_entry_pos(entry: HistoryEntry) -> int:
    """Извлекает числовое значение позиции из элемента истории."""
    match entry:
        case {"pos": int() as pos}:
            return pos
        case {"pos": str() as pos} if pos.isdigit():
            return int(pos)
        case str() as val:
            cleaned = val.rstrip("M")
            return int(cleaned) if cleaned.isdigit() else 0
        case int() as val:
            return val
        case _:
            return 0


def get_entry_status(entry: HistoryEntry) -> str:
    """Извлекает статус из элемента истории ('submitted' или 'missed')."""
    match entry:
        case {"status": str() as status}:
            return status
        case str() as val:
            return "missed" if val.endswith("M") else "submitted"
        case _:
            return "submitted"


async def ensure_submission_rows(
    db: AsyncSession, tg_ids: Sequence[int], subject_id: int
) -> dict[int, SubmissionAttempt]:
    """Гарантирует существование попыток сдачи для списка пользователей.

    Загружает существующие записи пачкой (IN clause), а недостающие
    создает через db.add_all() + db.begin_nested(), защищая от N+1 вызовов.

    Args:
        db: Асинхронная сессия БД.
        tg_ids: Последовательность Telegram ID пользователей.
        subject_id: ID предмета.

    Returns:
        dict[int, SubmissionAttempt]: Словарь {tg_id: SubmissionAttempt}.
    """
    if not tg_ids:
        return {}

    unique_tg_ids = list(set(tg_ids))
    stmt = select(SubmissionAttempt).where(
        SubmissionAttempt.tg_id.in_(unique_tg_ids),
        SubmissionAttempt.subject_id == subject_id,
    )
    result = await db.execute(stmt)
    existing_rows = {row.tg_id: row for row in result.scalars().all()}

    missing_ids = [uid for uid in unique_tg_ids if uid not in existing_rows]
    if missing_ids:
        new_attempts = [
            SubmissionAttempt(
                tg_id=uid,
                subject_id=subject_id,
                history_position=[],
                missed_attempts_count=0,
            )
            for uid in missing_ids
        ]
        try:
            async with db.begin_nested():
                db.add_all(new_attempts)
                await db.flush()
            for attempt in new_attempts:
                existing_rows[attempt.tg_id] = attempt
        except IntegrityError:
            result = await db.execute(stmt)
            existing_rows = {row.tg_id: row for row in result.scalars().all()}

    return existing_rows


async def ensure_submission_row(
    db: AsyncSession, tg_id: int, subject_id: int
) -> SubmissionAttempt:
    """Гарантирует существование записи попытки сдачи.

    Args:
        db: Асинхронная сессия БД.
        tg_id: Telegram ID пользователя.
        subject_id: ID предмета.

    Returns:
        SubmissionAttempt: Запись о попытках сдачи.
    """
    rows = await ensure_submission_rows(db, [tg_id], subject_id)
    row = rows.get(tg_id)
    if not row:
        raise ValueError(
            f"Не удалось получить или создать запись "
            f"SubmissionAttempt для tg_id={tg_id}"
        )
    return row


async def append_one_history_position(
    db: AsyncSession, tg_id: int, subject_id: int, position_label: str
) -> None:
    """Добавляет одну позицию в историю.

    Args:
        db: Асинхронная сессия БД.
        tg_id: Telegram ID пользователя.
        subject_id: ID предмета.
        position_label: Метка позиции.
    """
    submission_row = await ensure_submission_row(db, tg_id, subject_id)
    history_positions = list(submission_row.history_position or [])
    pos_val = (
        int(position_label) if position_label.isdigit() else position_label
    )
    history_positions.append({"pos": pos_val, "status": "submitted"})
    submission_row.history_position = history_positions
    flag_modified(submission_row, "history_position")
    await db.flush()


async def add_history_positions(
    db: AsyncSession, ordered_tg_ids: list[int], subject_id: int
) -> None:
    """Добавляет позиции в историю для списка пользователей.

    Args:
        db: Асинхронная сессия БД.
        ordered_tg_ids: Список Telegram ID в порядке очереди.
        subject_id: ID предмета.
    """
    if not ordered_tg_ids:
        return

    rows_by_tg_id = await ensure_submission_rows(
        db, ordered_tg_ids, subject_id
    )

    for index, tg_id in enumerate(ordered_tg_ids):
        submission_row = rows_by_tg_id.get(tg_id)
        if not submission_row:
            continue
        history_positions = list(submission_row.history_position or [])
        history_positions.append({"pos": index + 1, "status": "submitted"})
        submission_row.history_position = history_positions
        flag_modified(submission_row, "history_position")
    await db.flush()


async def _update_last_history_positions(
    db: AsyncSession,
    *,
    subject_id: int,
    updates: Sequence[tuple[int, int]],
    commit: bool = True,
) -> None:
    """Обновляет последнюю позицию в history_position для списка участников.

    Args:
        db: Асинхронная сессия БД.
        subject_id: ID предмета.
        updates: Последовательность пар (tg_id, new_pos_1based).
        commit: Выполнять ли flush сессии.
    """
    if not updates:
        return

    pos_by_tg_id = {tg_id: new_pos for tg_id, new_pos in updates}
    tg_ids = list(pos_by_tg_id.keys())

    submission_rows_by_id = await ensure_submission_rows(
        db, tg_ids, subject_id
    )

    for tg_id, submission_row in submission_rows_by_id.items():
        history_positions = list(submission_row.history_position or [])
        if not history_positions:
            continue

        new_pos = pos_by_tg_id[tg_id]
        last_entry = history_positions[-1]
        if isinstance(last_entry, dict):
            updated_entry = dict(last_entry)
            updated_entry["pos"] = new_pos
            history_positions[-1] = updated_entry
        else:
            history_positions[-1] = {"pos": new_pos, "status": "submitted"}

        submission_row.history_position = history_positions
        flag_modified(submission_row, "history_position")

    if commit:
        await db.flush()


async def sync_last_history_positions_after_swap(
    db: AsyncSession,
    *,
    subject_id: int,
    first_tg_id: int,
    first_new_pos_1based: int,
    second_tg_id: int,
    second_new_pos_1based: int,
    commit: bool = True,
) -> None:
    """Синхронизирует историю позиций после обмена.

    Переписывается последний элемент массива.
    Если истории нет/пуста — запись пропускается.

    Args:
        db: Асинхронная сессия БД.
        subject_id: ID предмета.
        first_tg_id: ID первого пользователя.
        first_new_pos_1based: Новая позиция первого.
        second_tg_id: ID второго пользователя.
        second_new_pos_1based: Новая позиция второго.
        commit: Выполнять ли flush.
    """
    pairs = (
        (first_tg_id, first_new_pos_1based),
        (second_tg_id, second_new_pos_1based),
    )
    await _update_last_history_positions(
        db, subject_id=subject_id, updates=pairs, commit=commit
    )


async def shift_last_history_positions_after_insert(
    db: AsyncSession,
    *,
    subject_id: int,
    shifted_participants_with_new_pos: list[tuple[int, int]],
    commit: bool = True,
) -> None:
    """Синхронизирует позицию в истории для сдвинувшихся участников.

    Args:
        db: Асинхронная сессия БД.
        subject_id: ID предмета.
        shifted_participants_with_new_pos: Список пар (tg_id, new_pos_1based).
        commit: Выполнять ли flush.
    """
    await _update_last_history_positions(
        db,
        subject_id=subject_id,
        updates=shifted_participants_with_new_pos,
        commit=commit,
    )


async def increment_missed_for_tg_ids(
    db: AsyncSession, tg_ids: list[int], subject_id: int
) -> None:
    """Увеличивает счетчик пропущенных попыток.

    Args:
        db: Асинхронная сессия БД.
        tg_ids: Список Telegram ID.
        subject_id: ID предмета.
    """
    if not tg_ids:
        return

    rows_by_id = await ensure_submission_rows(db, tg_ids, subject_id)
    for submission_row in rows_by_id.values():
        submission_row.missed_attempts_count = (
            int(submission_row.missed_attempts_count or 0) + 1
        )
    await db.flush()


async def apply_slot_penalties_after_last_submitter(
    db: AsyncSession,
    order: list[int | str],
    successful_slot_index: int,
    subject_id: int,
    pardoned_tg_ids: list[int] | None = None,
) -> None:
    """Применяет нереализованные попытки после последнего сдавшего в очереди.

    Args:
        db: Асинхронная сессия БД.
        order: Список участников очереди (ID или имена).
        successful_slot_index: Индекс последнего успешно сдавшего.
        subject_id: ID предмета.
        pardoned_tg_ids: Список прощенных Telegram ID.
    """
    if pardoned_tg_ids is None:
        pardoned_tg_ids = []

    pardoned_set = set(pardoned_tg_ids)

    successful_user_ids = {
        entry
        for entry in order[: successful_slot_index + 1]
        if isinstance(entry, int) and entry not in pardoned_set
    }

    unrealized_list = [
        entry
        for entry in order[successful_slot_index + 1 :]
        if isinstance(entry, int) and entry not in pardoned_set
    ]

    unrealized_counts = Counter(unrealized_list)

    if not unrealized_counts:
        return

    user_ids_to_update = list(unrealized_counts.keys())
    submission_rows_by_id = await ensure_submission_rows(
        db, user_ids_to_update, subject_id
    )

    for user_id, submission_row in submission_rows_by_id.items():
        missed_count = unrealized_counts[user_id]

        history_positions = list(submission_row.history_position or [])
        marked_count = 0
        for pos_index in range(len(history_positions) - 1, -1, -1):
            if marked_count >= missed_count:
                break
            entry = history_positions[pos_index]
            if get_entry_status(entry) != "missed":
                pos_val = get_entry_pos(entry)
                history_positions[pos_index] = {
                    "pos": pos_val,
                    "status": "missed",
                }
                marked_count += 1

        submission_row.history_position = history_positions
        flag_modified(submission_row, "history_position")

        if user_id not in successful_user_ids:
            submission_row.missed_attempts_count = (
                int(submission_row.missed_attempts_count or 0) + missed_count
            )

    await db.flush()
