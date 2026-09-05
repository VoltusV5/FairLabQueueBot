"""Тесты для репозитория опросов присутствия (presence.py)."""

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import PresencePoll
from src.db.repositories.presence import (
    create_presence_poll,
    get_presence_poll,
    upsert_presence_here,
)


@pytest.mark.asyncio
async def test_create_and_get_presence_poll(
    async_session: AsyncSession,
) -> None:
    """Проверяет создание и получение опроса присутствия."""
    not_found = await get_presence_poll(async_session, 100, 555)
    assert not_found is None

    poll = await create_presence_poll(
        async_session, chat_id=100, message_id=555, flush=True
    )
    assert poll is not None
    assert poll.chat_id == 100
    assert poll.message_id == 555
    assert poll.here_tg_ids == []

    fetched = await get_presence_poll(async_session, 100, 555, for_update=True)
    assert fetched is not None
    assert fetched.chat_id == 100


@pytest.mark.asyncio
async def test_upsert_presence_here_add_and_remove(
    async_session: AsyncSession,
) -> None:
    """Проверяет добавление и удаление пользователей из опроса присутствия."""
    chat_id = 200
    msg_id = 777
    user_id = 123456

    # Добавление пользователя в новый опрос
    changed = await upsert_presence_here(
        async_session, chat_id, msg_id, user_id, add=True
    )
    assert changed is True

    poll = await get_presence_poll(async_session, chat_id, msg_id)
    assert poll is not None
    assert user_id in poll.here_tg_ids

    # Повторное добавление того же пользователя -> False
    changed_again = await upsert_presence_here(
        async_session, chat_id, msg_id, user_id, add=True
    )
    assert changed_again is False

    # Удаление пользователя из опроса
    removed = await upsert_presence_here(
        async_session, chat_id, msg_id, user_id, add=False
    )
    assert removed is True

    poll_after_remove = await get_presence_poll(async_session, chat_id, msg_id)
    assert poll_after_remove is not None
    assert user_id not in poll_after_remove.here_tg_ids

    # Повторное удаление -> False
    removed_again = await upsert_presence_here(
        async_session, chat_id, msg_id, user_id, add=False
    )
    assert removed_again is False


@pytest.mark.asyncio
async def test_upsert_presence_here_integrity_error_fallback(
    async_session: AsyncSession,
) -> None:
    """Проверяет обработку IntegrityError при гонке создания опроса."""
    chat_id = 300
    msg_id = 888
    user_id = 9999

    # Создаем опрос заранее (эмуляция конкурентного потока)
    await create_presence_poll(async_session, chat_id, msg_id, flush=True)

    original_get = get_presence_poll
    original_flush = async_session.flush

    get_responses = [None]
    flush_errors = [IntegrityError("stmt", {}, Exception("Duplicate key"))]

    async def mock_get(
        db: AsyncSession, c_id: int, m_id: int, *, for_update: bool = False
    ) -> PresencePoll | None:
        if get_responses:
            return get_responses.pop(0)
        return await original_get(db, c_id, m_id, for_update=for_update)

    async def mock_flush(*args: Any, **kwargs: Any) -> Any:
        if flush_errors:
            raise flush_errors.pop(0)
        return await original_flush(*args, **kwargs)

    with (
        patch(
            "src.db.repositories.presence.get_presence_poll",
            side_effect=mock_get,
        ),
        patch.object(
            async_session,
            "flush",
            side_effect=mock_flush,
        ),
    ):
        changed = await upsert_presence_here(
            async_session, chat_id, msg_id, user_id, add=True
        )
        assert changed is True

    poll = await original_get(async_session, chat_id, msg_id)
    assert poll is not None
    assert user_id in poll.here_tg_ids
