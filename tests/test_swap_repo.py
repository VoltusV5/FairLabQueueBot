"""Интеграционные тесты для репозитория заявок на обмен (swap.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import SwapStatus
from src.db.repositories.subject import get_or_create_subject
from src.db.repositories.swap import (
    complete_swap,
    create_formed_swap_request,
    delete_swap_request_row,
    delete_swaps_for_queue,
    delete_swaps_pending_for_queue,
    find_open_swap_for_message,
    get_swap_request,
    mark_swap_done,
    open_swap,
    set_swap_request_message_id,
)

CHAT_ID = 100
QUEUE_MESSAGE_ID = 500
ALICE_ID = 1001
BOB_ID = 1002


@pytest.fixture
async def subject_id(async_session: AsyncSession) -> int:
    """Фикстура для создания тестового предмета."""
    subject = await get_or_create_subject(async_session, CHAT_ID, "Math")
    await async_session.flush()
    return subject.id


async def test_open_swap_and_find(
    async_session: AsyncSession, subject_id: int
) -> None:
    """Проверяет создание и поиск открытой заявки на обмен."""
    # 1. Создаем открытую заявку
    swap_req = await open_swap(
        async_session,
        chat_id=CHAT_ID,
        queue_message_id=QUEUE_MESSAGE_ID,
        subject_id=subject_id,
        from_tg_id=ALICE_ID,
        swap_message_id=777,
    )
    assert swap_req.id is not None
    assert swap_req.status == SwapStatus.OPEN.value
    assert swap_req.to_tg_id is None

    # 2. Ищем открытую заявку
    found = await find_open_swap_for_message(async_session, QUEUE_MESSAGE_ID)
    assert found is not None
    assert found.id == swap_req.id

    found_lock = await find_open_swap_for_message(
        async_session, QUEUE_MESSAGE_ID, for_update=True
    )
    assert found_lock is not None
    assert found_lock.id == swap_req.id


async def test_find_open_swap_for_message_negative_filters(
    async_session: AsyncSession, subject_id: int
) -> None:
    """Негативные тесты: фильтрация по статусу."""
    formed_req = await create_formed_swap_request(
        async_session,
        CHAT_ID,
        QUEUE_MESSAGE_ID,
        subject_id,
        ALICE_ID,
        BOB_ID,
        200,
    )
    assert (
        await find_open_swap_for_message(async_session, QUEUE_MESSAGE_ID)
        is None
    )

    await mark_swap_done(async_session, formed_req)
    assert (
        await find_open_swap_for_message(async_session, QUEUE_MESSAGE_ID)
        is None
    )

    assert await find_open_swap_for_message(async_session, 999999) is None


async def test_create_formed_swap_and_get(
    async_session: AsyncSession, subject_id: int
) -> None:
    """Проверяет создание адресной заявки на обмен и получение по ID."""
    swap_req = await create_formed_swap_request(
        async_session,
        chat_id=CHAT_ID,
        queue_message_id=QUEUE_MESSAGE_ID,
        subject_id=subject_id,
        from_tg_id=ALICE_ID,
        to_tg_id=BOB_ID,
        confirm_message_id=888,
    )
    assert swap_req.status == SwapStatus.AWAIT_ACCEPT.value
    assert swap_req.to_tg_id == BOB_ID

    fetched = await get_swap_request(async_session, swap_req.id)
    assert fetched is not None
    assert fetched.id == swap_req.id

    fetched_locked = await get_swap_request(
        async_session, swap_req.id, for_update=True
    )
    assert fetched_locked is not None
    assert fetched_locked.id == swap_req.id


async def test_get_swap_request_non_existent(
    async_session: AsyncSession,
) -> None:
    """Негативные тесты: запросы несуществующих ID."""
    assert await get_swap_request(async_session, 999999) is None
    assert (
        await get_swap_request(async_session, 999999, for_update=True) is None
    )


async def test_swap_lifecycle_mutations(
    async_session: AsyncSession, subject_id: int
) -> None:
    """Проверяет мутацию полей заявки на обмен."""
    swap_req = await open_swap(
        async_session,
        chat_id=CHAT_ID,
        queue_message_id=QUEUE_MESSAGE_ID,
        subject_id=subject_id,
        from_tg_id=ALICE_ID,
        swap_message_id=111,
    )

    # Обновление ID сообщения
    await set_swap_request_message_id(async_session, swap_req, 999)
    assert swap_req.swap_message_id == 999

    # Завершение обмена с указанием второго участника
    await complete_swap(async_session, swap_req, BOB_ID)
    assert swap_req.status == SwapStatus.DONE.value
    assert swap_req.to_tg_id == BOB_ID

    # Повторная пометка выполненным
    await mark_swap_done(async_session, swap_req)
    assert swap_req.status == SwapStatus.DONE.value


async def test_delete_swaps(
    async_session: AsyncSession, subject_id: int
) -> None:
    """Проверяет точечное и массовое удаление заявок."""
    # 1. Создаем 3 заявки: одну open, одну await_accept, одну done
    open_req = await open_swap(
        async_session, CHAT_ID, QUEUE_MESSAGE_ID, subject_id, ALICE_ID, 10
    )
    formed_req = await create_formed_swap_request(
        async_session,
        CHAT_ID,
        QUEUE_MESSAGE_ID,
        subject_id,
        ALICE_ID,
        BOB_ID,
        20,
    )
    done_req = await open_swap(
        async_session, CHAT_ID, QUEUE_MESSAGE_ID, subject_id, ALICE_ID, 30
    )
    await mark_swap_done(async_session, done_req)

    # 2. Удаляем точечно 1 заявку
    await delete_swap_request_row(async_session, open_req)
    assert await get_swap_request(async_session, open_req.id) is None

    # 3. Удаляем только незавершенные обмены (open, await_accept)
    await delete_swaps_pending_for_queue(async_session, QUEUE_MESSAGE_ID)
    assert await get_swap_request(async_session, formed_req.id) is None

    assert await get_swap_request(async_session, done_req.id) is not None

    # 4. Удаляем все обмены очереди
    await delete_swaps_for_queue(async_session, QUEUE_MESSAGE_ID)
    assert await get_swap_request(async_session, done_req.id) is None
