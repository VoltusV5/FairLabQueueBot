"""Интеграционные тесты очередей (src/db/repositories/queue.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import Queue, QueueStatus, Subject, SubmissionAttempt
from src.db.repositories.queue import (
    ParticipantAlreadyExistsError,
    ParticipantNotFoundError,
    QueueStatusError,
    add_participant,
    add_queue_row,
    complete_queue_last_submitter,
    delete_queue_row,
    get_queue_by_chat_message,
    insert_into_formed_queue,
    is_queue_duplicate,
    list_queues_recruiting,
    list_queues_waiting_last,
    merge_extra,
    pardon_queue_participant,
    remove_participant,
    rollback_and_delete_queue,
)

CHAT_ID = 100
MSG_ID = 500
LESSON_DT = datetime(2026, 9, 1, 10, 0, 0)

ALICE = 1001
BOB = 1002
CAROL = 1003

_PENALTIES_PATH = (
    "src.db.repositories.queue.apply_slot_penalties_after_last_submitter"
)
_APPEND_HISTORY_PATH = "src.db.repositories.queue.append_one_history_position"
_SHIFT_HISTORY_PATH = (
    "src.db.repositories.queue.shift_last_history_positions_after_insert"
)


@pytest.fixture
async def subject(async_session: AsyncSession) -> Subject:
    """Создаёт тестовый предмет в БД."""
    new_subject = Subject(chat_id=CHAT_ID, subject_name="Физика")
    async_session.add(new_subject)
    await async_session.flush()
    await async_session.refresh(new_subject)
    return new_subject


@pytest.fixture
async def make_queue(
    async_session: AsyncSession, subject: Subject
) -> Callable[..., Awaitable[Queue]]:
    """Фабрика тестовых очередей с переопределяемыми параметрами."""

    async def _factory(
        *,
        status: QueueStatus | str = QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants: list[int] | None = None,
        chat_id: int = CHAT_ID,
        message_id: int = MSG_ID,
        lesson_date: datetime = LESSON_DT,
        extra: dict[str, Any] | None = None,
    ) -> Queue:
        return await add_queue_row(
            async_session,
            subject_id=subject.id,
            chat_id=chat_id,
            message_id=message_id,
            lesson_date=lesson_date,
            close_at=None,
            status=status,
            participants=participants or [],
            extra=extra,
        )

    return _factory


@pytest.fixture
def mock_slot_penalties() -> Generator[AsyncMock, None, None]:
    """Мок apply_slot_penalties_after_last_submitter."""
    with patch(_PENALTIES_PATH, new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_history_fns() -> Generator[tuple[AsyncMock, AsyncMock], None, None]:
    """Моки append_one_history_position и shift_last...after_insert."""
    with (
        patch(_APPEND_HISTORY_PATH, new_callable=AsyncMock) as mock_append,
        patch(_SHIFT_HISTORY_PATH, new_callable=AsyncMock) as mock_shift,
    ):
        yield mock_append, mock_shift


async def test_is_duplicate_false_when_empty(
    async_session: AsyncSession, subject: Subject
) -> None:
    """Возвращает False, если очереди для предмета на эту дату нет."""
    is_duplicate = await is_queue_duplicate(
        async_session, subject.id, CHAT_ID, LESSON_DT
    )
    assert is_duplicate is False


async def test_is_duplicate_true_after_creation(
    async_session: AsyncSession, make_queue: Callable[..., Awaitable[Queue]]
) -> None:
    """Возвращает True после создания очереди."""
    queue = await make_queue()
    await async_session.commit()

    is_duplicate = await is_queue_duplicate(
        async_session, queue.subject_id, CHAT_ID, LESSON_DT
    )
    assert is_duplicate is True


async def test_is_duplicate_different_chat(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
    subject: Subject,
) -> None:
    """Возвращает False, если очередь для другого чата."""
    await make_queue()
    await async_session.commit()

    is_duplicate = await is_queue_duplicate(
        async_session, subject.id, 9999, LESSON_DT
    )
    assert is_duplicate is False


async def test_add_queue_row_persists_to_db(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Создаёт запись и проверяет её наличие в БД после коммита."""
    created_queue = await make_queue(participants=[ALICE, BOB])
    await async_session.commit()

    await async_session.refresh(created_queue)

    assert created_queue.participants == [ALICE, BOB]
    assert created_queue.status == QueueStatus.WAITING_FOR_PARTICIPANTS


async def test_get_queue_by_chat_message_not_found(
    async_session: AsyncSession,
) -> None:
    """Возвращает None, если очередь не найдена."""
    missing_queue = await get_queue_by_chat_message(
        async_session, CHAT_ID, 99999
    )
    assert missing_queue is None


async def test_get_queue_for_update(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """for_update=True выполняется без ошибок в тестовом движке."""
    await make_queue()
    await async_session.commit()

    locked_queue = await get_queue_by_chat_message(
        async_session, CHAT_ID, MSG_ID, for_update=True
    )
    assert locked_queue is not None


def test_merge_extra_creates_new_keys() -> None:
    """Добавляет новые ключи, не трогая существующие."""
    queue = Queue(extra={"key_a": 1})
    merge_extra(queue, {"key_b": 2})
    assert queue.extra == {"key_a": 1, "key_b": 2}


def test_merge_extra_overwrites_existing_key() -> None:
    """Перезаписывает уже существующий ключ."""
    queue = Queue(extra={"key_a": 1})
    merge_extra(queue, {"key_a": 99})
    assert queue.extra is not None
    assert queue.extra["key_a"] == 99


def test_merge_extra_on_none_extra() -> None:
    """Корректно обрабатывает extra = None."""
    queue = Queue(extra=None)
    merge_extra(queue, {"new_key": "value"})
    assert queue.extra == {"new_key": "value"}


async def test_add_participant_appends_to_list(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Добавляет участника и сохраняет изменения в БД."""
    queue = await make_queue()
    await add_participant(async_session, queue, ALICE)
    await async_session.commit()

    await async_session.refresh(queue)
    assert ALICE in queue.participants


async def test_add_participant_duplicate_raises(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Бросает ParticipantAlreadyExistsError при повторном добавлении."""
    queue = await make_queue(participants=[ALICE])
    with pytest.raises(ParticipantAlreadyExistsError):
        await add_participant(async_session, queue, ALICE)


async def test_remove_participant_returns_true(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Возвращает True и удаляет участника из списка."""
    queue = await make_queue(participants=[ALICE, BOB])
    was_removed = await remove_participant(async_session, queue, ALICE)
    await async_session.commit()

    assert was_removed is True
    await async_session.refresh(queue)
    assert ALICE not in queue.participants
    assert BOB in queue.participants


async def test_remove_participant_not_present_returns_false(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Возвращает False, если участника нет в очереди."""
    queue = await make_queue(participants=[BOB])
    was_removed = await remove_participant(async_session, queue, ALICE)
    assert was_removed is False


async def test_complete_queue_last_submitter_happy_path(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
    mock_slot_penalties: AsyncMock,
) -> None:
    """Сохраняет successful_slot_index и статус COMPLETED."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB, CAROL],
    )
    subject_id = queue.subject_id

    await complete_queue_last_submitter(async_session, queue, BOB)

    await async_session.commit()
    await async_session.refresh(queue)

    assert queue.status == QueueStatus.COMPLETED
    assert queue.extra is not None
    assert queue.extra["successful_slot_index"] == 1
    mock_slot_penalties.assert_awaited_once_with(
        async_session,
        [ALICE, BOB, CAROL],
        1,
        subject_id,
        [],
    )


async def test_complete_queue_wrong_status_raises(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Бросает QueueStatusError при неверном статусе очереди."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants=[ALICE],
    )
    with pytest.raises(QueueStatusError):
        await complete_queue_last_submitter(async_session, queue, ALICE)


async def test_complete_queue_user_not_found_raises(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Бросает ParticipantNotFoundError, если участника нет."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB],
    )
    with pytest.raises(ParticipantNotFoundError):
        await complete_queue_last_submitter(async_session, queue, CAROL)


async def test_complete_queue_picks_last_index_for_duplicate_user(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
    mock_slot_penalties: AsyncMock,
) -> None:
    """При дублирующемся tg_id берёт последний (самый правый) индекс."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, ALICE, BOB],
    )
    subject_id = queue.subject_id

    await complete_queue_last_submitter(async_session, queue, ALICE)

    assert queue.extra is not None
    assert queue.extra["successful_slot_index"] == 1
    mock_slot_penalties.assert_awaited_once_with(
        async_session,
        [ALICE, ALICE, BOB],
        1,
        subject_id,
        [],
    )


async def test_insert_into_formed_queue_at_position_1(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
    mock_history_fns: tuple[AsyncMock, AsyncMock],
) -> None:
    """Вставка на первую позицию сдвигает остальных вправо."""
    mock_append, mock_shift = mock_history_fns
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB],
    )
    subject_id = queue.subject_id

    await insert_into_formed_queue(async_session, queue, CAROL, pos_1based=1)

    assert queue.participants == [CAROL, ALICE, BOB]
    mock_append.assert_awaited_once_with(async_session, CAROL, subject_id, "1")
    # ALICE сдвигается с позиции 1 -> 2, BOB с 2 -> 3
    mock_shift.assert_awaited_once_with(
        async_session,
        subject_id=subject_id,
        shifted_participants_with_new_pos=[(ALICE, 2), (BOB, 3)],
    )


async def test_insert_into_formed_queue_at_last_position(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
    mock_history_fns: tuple[AsyncMock, AsyncMock],
) -> None:
    """Вставка в конец (N+1) не вызывает сдвиг."""
    mock_append, mock_shift = mock_history_fns
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB],
    )
    subject_id = queue.subject_id

    await insert_into_formed_queue(async_session, queue, CAROL, pos_1based=3)

    assert queue.participants == [ALICE, BOB, CAROL]
    mock_append.assert_awaited_once_with(async_session, CAROL, subject_id, "3")
    mock_shift.assert_not_awaited()


@pytest.mark.parametrize("invalid_pos", [0, -1, 4])
async def test_insert_into_formed_queue_invalid_pos_raises(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
    invalid_pos: int,
) -> None:
    """Бросает ValueError для позиций за пределами [1, N+1]."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB],
    )
    with pytest.raises(ValueError, match="Неверный номер места"):
        await insert_into_formed_queue(
            async_session, queue, CAROL, pos_1based=invalid_pos
        )


async def test_insert_into_formed_queue_duplicate_raises(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Бросает ParticipantAlreadyExistsError, если участник в очереди."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB],
    )
    with pytest.raises(ParticipantAlreadyExistsError):
        await insert_into_formed_queue(
            async_session, queue, ALICE, pos_1based=2
        )


async def test_insert_into_formed_queue_wrong_status_raises(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Бросает QueueStatusError при неправильном статусе очереди."""
    queue = await make_queue(
        status=QueueStatus.WAITING_FOR_PARTICIPANTS,
        participants=[ALICE],
    )
    with pytest.raises(QueueStatusError):
        await insert_into_formed_queue(async_session, queue, BOB, pos_1based=1)


async def test_delete_queue_row(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Удаляет очередь из БД."""
    queue = await make_queue()
    await async_session.commit()

    await delete_queue_row(async_session, queue)
    await async_session.commit()

    deleted_queue = await get_queue_by_chat_message(
        async_session, CHAT_ID, MSG_ID
    )
    assert deleted_queue is None


async def test_list_queues_recruiting(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Возвращает только очереди в статусе WAITING_FOR_PARTICIPANTS."""
    date_recruiting = datetime(2026, 9, 1, 10, 0, 0)
    date_waiting = datetime(2026, 9, 2, 10, 0, 0)
    await make_queue(
        status=QueueStatus.WAITING_FOR_PARTICIPANTS,
        message_id=1,
        lesson_date=date_recruiting,
    )
    await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        message_id=2,
        lesson_date=date_waiting,
    )
    await async_session.commit()

    recruiting_queues = await list_queues_recruiting(async_session)
    assert len(recruiting_queues) == 1
    assert recruiting_queues[0].status == QueueStatus.WAITING_FOR_PARTICIPANTS


async def test_list_queues_waiting_last(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Возвращает только очереди в статусе WAITING_FOR_LAST_PARTICIPANT."""
    date_recruiting = datetime(2026, 9, 1, 10, 0, 0)
    date_waiting = datetime(2026, 9, 2, 10, 0, 0)
    await make_queue(
        status=QueueStatus.WAITING_FOR_PARTICIPANTS,
        message_id=1,
        lesson_date=date_recruiting,
    )
    await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        message_id=2,
        lesson_date=date_waiting,
    )
    await async_session.commit()

    waiting_queues = await list_queues_waiting_last(async_session)
    assert len(waiting_queues) == 1
    assert waiting_queues[0].status == QueueStatus.WAITING_FOR_LAST_PARTICIPANT


async def test_list_queues_empty(async_session: AsyncSession) -> None:
    """Возвращает пустые списки, если очередей нет."""
    assert await list_queues_recruiting(async_session) == []
    assert await list_queues_waiting_last(async_session) == []


async def test_pardon_queue_participant_waiting(
    async_session: AsyncSession,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Проверяет помилование участника в ожидающей очереди."""
    q = await make_queue(
        status=QueueStatus.WAITING_FOR_LAST_PARTICIPANT,
        participants=[ALICE, BOB],
    )
    await async_session.commit()

    ok, code = await pardon_queue_participant(async_session, q, ALICE)
    assert ok is True
    assert code == "pardoned_in_advance"
    assert ALICE in (q.extra or {}).get("pardoned_tg_ids", [])


async def test_pardon_queue_participant_completed(
    async_session: AsyncSession,
    subject: Subject,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Проверяет ретроактивное помилование в завершенной очереди."""
    q = await make_queue(
        status=QueueStatus.COMPLETED,
        participants=[ALICE, BOB],
        extra={"successful_slot_index": 0},
    )
    # Создаем историю сдачи для ALICE
    sub = SubmissionAttempt(
        tg_id=ALICE,
        subject_id=subject.id,
        history_position=[{"pos": 1, "status": "submitted"}],
        missed_attempts_count=0,
    )
    async_session.add(sub)
    await async_session.commit()

    ok, code = await pardon_queue_participant(async_session, q, ALICE)
    assert ok is True
    assert code == "retroactive_pardon"
    assert ALICE in (q.extra or {}).get("pardoned_tg_ids", [])
    assert sub.missed_attempts_count == 1
    assert sub.history_position[0]["status"] == "missed"

    # Повторный вызов возвращает already_pardoned
    ok_again, code_again = await pardon_queue_participant(
        async_session, q, ALICE
    )
    assert ok_again is False
    assert code_again == "already_pardoned"


async def test_rollback_and_delete_queue(
    async_session: AsyncSession,
    subject: Subject,
    make_queue: Callable[..., Awaitable[Queue]],
) -> None:
    """Проверяет удаление очереди с откатом истории попыток."""
    q = await make_queue(
        status=QueueStatus.COMPLETED,
        participants=[ALICE],
        extra={"successful_slot_index": 0},
    )
    sub = SubmissionAttempt(
        tg_id=ALICE,
        subject_id=subject.id,
        history_position=[{"pos": 1, "status": "submitted"}],
        missed_attempts_count=0,
    )
    async_session.add(sub)
    await async_session.commit()

    await rollback_and_delete_queue(async_session, q)
    await async_session.commit()

    # Очередь должна быть удалена
    q_check = await get_queue_by_chat_message(
        async_session, CHAT_ID, q.message_id
    )
    assert q_check is None
    # История позиции должна быть очищена
    assert sub.history_position == []
