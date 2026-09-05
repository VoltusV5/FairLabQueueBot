"""Интеграционные и модульные тесты для submission repository."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.repositories.submission import (
    add_history_positions,
    append_one_history_position,
    apply_slot_penalties_after_last_submitter,
    ensure_submission_row,
    ensure_submission_rows,
    get_entry_pos,
    get_entry_status,
    increment_missed_for_tg_ids,
    shift_last_history_positions_after_insert,
    sync_last_history_positions_after_swap,
)

SUBJECT_ID = 1
ALICE_ID = 1001
BOB_ID = 1002
CHARLIE_ID = 1003


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"pos": 5, "status": "submitted"}, 5),
        ({"pos": "3", "status": "missed"}, 3),
        ("4M", 4),
        ("10", 10),
        (7, 7),
        ({"status": "missed"}, 0),
        ("invalid", 0),
        (None, 0),
        (3.14, 0),
    ],
)
def test_get_entry_pos_parametrized(entry: Any, expected: int) -> None:
    """Проверяет работы get_entry_pos с различными типами записей."""
    assert get_entry_pos(entry) == expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"status": "missed"}, "missed"),
        ({"status": "submitted"}, "submitted"),
        ("5M", "missed"),
        ("5", "submitted"),
        (None, "submitted"),
        ({"pos": 5}, "submitted"),
    ],
)
def test_get_entry_status_parametrized(entry: Any, expected: str) -> None:
    """Проверяет работу get_entry_status с различными типами записей."""
    assert get_entry_status(entry) == expected


async def test_ensure_submission_rows_and_delegation(
    async_session: AsyncSession,
) -> None:
    """Проверяет работу ensure_submission_rows и вызов
    ensure_submission_row.
    """
    rows = await ensure_submission_rows(
        async_session, [ALICE_ID, BOB_ID], SUBJECT_ID
    )
    assert len(rows) == 2
    assert rows[ALICE_ID].tg_id == ALICE_ID
    assert rows[BOB_ID].tg_id == BOB_ID

    single_row = await ensure_submission_row(
        async_session, ALICE_ID, SUBJECT_ID
    )
    assert single_row.id == rows[ALICE_ID].id


async def test_ensure_submission_rows_empty(
    async_session: AsyncSession,
) -> None:
    """Проверяет обработку пустого списка tg_ids."""
    rows = await ensure_submission_rows(async_session, [], SUBJECT_ID)
    assert rows == {}


async def test_increment_missed_for_tg_ids_repeated(
    async_session: AsyncSession,
) -> None:
    """Проверяет инкремент пропущенных попыток, включая
    повторный (0 -> 1 -> 2).
    """
    # Первый вызов: 0 -> 1
    await increment_missed_for_tg_ids(
        async_session, [ALICE_ID, BOB_ID], SUBJECT_ID
    )
    rows = await ensure_submission_rows(
        async_session, [ALICE_ID, BOB_ID], SUBJECT_ID
    )
    assert rows[ALICE_ID].missed_attempts_count == 1

    # Второй вызов: 1 -> 2
    await increment_missed_for_tg_ids(async_session, [ALICE_ID], SUBJECT_ID)
    rows = await ensure_submission_rows(
        async_session, [ALICE_ID, BOB_ID], SUBJECT_ID
    )
    assert rows[ALICE_ID].missed_attempts_count == 2
    assert rows[BOB_ID].missed_attempts_count == 1


async def test_increment_missed_for_tg_ids_empty(
    async_session: AsyncSession,
) -> None:
    """Проверяет, что пустой список не вызывает ошибок."""
    await increment_missed_for_tg_ids(async_session, [], SUBJECT_ID)
    # тест пройден.


async def test_apply_slot_penalties_after_last_submitter(
    async_session: AsyncSession,
) -> None:
    """Проверяет начисление штрафов с учетом истории, прощенных и строк."""
    order = [ALICE_ID, "guest_name", BOB_ID, BOB_ID, CHARLIE_ID]

    await add_history_positions(
        async_session,
        ordered_tg_ids=[ALICE_ID, BOB_ID, CHARLIE_ID],
        subject_id=SUBJECT_ID,
    )

    await append_one_history_position(async_session, BOB_ID, SUBJECT_ID, "2")

    await apply_slot_penalties_after_last_submitter(
        async_session,
        order=order,
        successful_slot_index=0,
        subject_id=SUBJECT_ID,
        pardoned_tg_ids=[CHARLIE_ID],
    )

    rows = await ensure_submission_rows(
        async_session, [ALICE_ID, BOB_ID, CHARLIE_ID], SUBJECT_ID
    )
    assert rows[ALICE_ID].missed_attempts_count == 0
    assert rows[BOB_ID].missed_attempts_count == 2
    assert rows[CHARLIE_ID].missed_attempts_count == 0

    # Проверка мутации истории позиций (history_position)
    alice_hist = rows[ALICE_ID].history_position
    assert alice_hist and len(alice_hist) == 1
    assert get_entry_status(alice_hist[0]) == "submitted"

    bob_hist = rows[BOB_ID].history_position
    assert bob_hist and len(bob_hist) == 2
    assert get_entry_status(bob_hist[0]) == "missed"
    assert get_entry_status(bob_hist[1]) == "missed"

    charlie_hist = rows[CHARLIE_ID].history_position
    assert charlie_hist and len(charlie_hist) == 1
    assert get_entry_status(charlie_hist[0]) == "submitted"


async def test_sync_last_history_positions_after_swap(
    async_session: AsyncSession,
) -> None:
    """Проверяет синхронизацию последних позиций после обмена местами."""

    await add_history_positions(
        async_session,
        ordered_tg_ids=[ALICE_ID, BOB_ID],
        subject_id=SUBJECT_ID,
    )

    await sync_last_history_positions_after_swap(
        async_session,
        subject_id=SUBJECT_ID,
        first_tg_id=ALICE_ID,
        first_new_pos_1based=2,
        second_tg_id=BOB_ID,
        second_new_pos_1based=1,
    )

    rows = await ensure_submission_rows(
        async_session, [ALICE_ID, BOB_ID], SUBJECT_ID
    )
    assert get_entry_pos(rows[ALICE_ID].history_position[-1]) == 2
    assert get_entry_pos(rows[BOB_ID].history_position[-1]) == 1


async def test_shift_last_history_positions_after_insert(
    async_session: AsyncSession,
) -> None:
    """Проверяет сдвиг позиций после вставки участника."""
    # 1. Создаем записи с историей: Alice (1), Bob (2), Charlie (3)
    await add_history_positions(
        async_session,
        ordered_tg_ids=[ALICE_ID, BOB_ID, CHARLIE_ID],
        subject_id=SUBJECT_ID,
    )

    # 2. Допустим, Чарли прыгнул на место 1, сдвинув Алису и Боба.
    await shift_last_history_positions_after_insert(
        async_session,
        subject_id=SUBJECT_ID,
        shifted_participants_with_new_pos=[(ALICE_ID, 2), (BOB_ID, 3)],
    )

    # 3. Проверяем новые позиции
    rows = await ensure_submission_rows(
        async_session, [ALICE_ID, BOB_ID, CHARLIE_ID], SUBJECT_ID
    )
    assert get_entry_pos(rows[ALICE_ID].history_position[-1]) == 2
    assert get_entry_pos(rows[BOB_ID].history_position[-1]) == 3
    # Чарли остается на своей старой позиции в истории (3),
    # так как вставка сама по себе не меняет его историю
    # (его история будет обновлена отдельно в бизнес-логике)
    assert get_entry_pos(rows[CHARLIE_ID].history_position[-1]) == 3
