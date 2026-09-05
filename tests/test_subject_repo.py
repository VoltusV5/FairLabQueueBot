"""Интеграционные тесты репозитория предметов."""

from typing import Any
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.repositories.subject import (
    add_subject_king,
    get_or_create_subject,
    get_subject_by_id,
    get_subject_by_name,
    list_subject_names_for_chat,
    remove_subject_king,
)

CHAT_ID = 100
OTHER_CHAT_ID = 200


async def test_list_subject_names_for_chat_empty(
    async_session: AsyncSession,
) -> None:
    """Возвращает пустой список, если у чата нет предметов."""
    subject_names = await list_subject_names_for_chat(async_session, CHAT_ID)
    assert subject_names == []


async def test_list_subject_names_for_chat_sorted_case_insensitive(
    async_session: AsyncSession,
) -> None:
    """Возвращает названия предметов, отсортированные без учёта регистра."""
    await get_or_create_subject(async_session, CHAT_ID, "Physics")
    await get_or_create_subject(async_session, CHAT_ID, "algebra")
    await get_or_create_subject(async_session, CHAT_ID, "Math")
    await async_session.commit()

    subject_names = await list_subject_names_for_chat(async_session, CHAT_ID)
    assert subject_names == ["algebra", "Math", "Physics"]


async def test_list_subject_names_for_chat_filters_by_chat_id(
    async_session: AsyncSession,
) -> None:
    """Возвращает предметы только указанного чата."""
    await get_or_create_subject(async_session, CHAT_ID, "Физика")
    await get_or_create_subject(async_session, OTHER_CHAT_ID, "Химия")
    await async_session.commit()

    subject_names = await list_subject_names_for_chat(async_session, CHAT_ID)
    assert subject_names == ["Физика"]


async def test_get_or_create_subject_creates_new(
    async_session: AsyncSession,
) -> None:
    """Создаёт новый предмет, если он отсутствует."""
    created_subject = await get_or_create_subject(
        async_session, CHAT_ID, "Информатика"
    )
    await async_session.commit()

    assert created_subject.id is not None
    assert created_subject.chat_id == CHAT_ID
    assert created_subject.subject_name == "Информатика"


async def test_get_or_create_subject_returns_existing(
    async_session: AsyncSession,
) -> None:
    """Возвращает существующий предмет при повторном вызове."""
    initial_subject = await get_or_create_subject(
        async_session, CHAT_ID, "История"
    )
    await async_session.commit()

    fetched_subject = await get_or_create_subject(
        async_session, CHAT_ID, "История"
    )

    assert fetched_subject.id == initial_subject.id
    assert fetched_subject.subject_name == "История"


async def test_get_or_create_subject_handles_race_condition(
    async_session: AsyncSession,
) -> None:
    """Обрабатывает IntegrityError при одновременном создании."""
    existing = await get_or_create_subject(async_session, CHAT_ID, "География")
    await async_session.commit()
    async_session.expire_all()

    original_flush = async_session.flush
    call_count = 0

    async def mock_flush(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise IntegrityError(
                "stmt", {}, Exception("UNIQUE constraint failed")
            )
        return await original_flush(*args, **kwargs)

    with patch.object(async_session, "flush", side_effect=mock_flush):
        result_subject = await get_or_create_subject(
            async_session, CHAT_ID, "География"
        )
        assert result_subject.id == existing.id


async def test_get_subject_by_id_found(
    async_session: AsyncSession,
) -> None:
    """Находит предмет по его уникальному ID."""
    created_subject = await get_or_create_subject(
        async_session, CHAT_ID, "Биология"
    )
    await async_session.commit()

    found_subject = await get_subject_by_id(async_session, created_subject.id)

    assert found_subject is not None
    assert found_subject.id == created_subject.id
    assert found_subject.subject_name == "Биология"


async def test_get_subject_by_id_uses_identity_map(
    async_session: AsyncSession,
) -> None:
    """Возвращает кэшированный объект из Identity Map без доп. запросов."""
    created = await get_or_create_subject(async_session, CHAT_ID, "Экология")
    await async_session.flush()

    cached_subject = await get_subject_by_id(async_session, created.id)
    assert cached_subject is created


async def test_get_subject_by_id_not_found(
    async_session: AsyncSession,
) -> None:
    """Возвращает None для несуществующего ID."""
    found_subject = await get_subject_by_id(async_session, 99999)
    assert found_subject is None


async def test_get_or_create_subject_trims_whitespace(
    async_session: AsyncSession,
) -> None:
    """Обрезает внешние пробелы в названии предмета при создании и поиске."""
    created = await get_or_create_subject(
        async_session, CHAT_ID, "  Математика  "
    )
    await async_session.commit()

    assert created.subject_name == "Математика"

    fetched = await get_or_create_subject(async_session, CHAT_ID, "Математика")
    assert fetched.id == created.id


async def test_get_subject_by_name_found_and_not_found(
    async_session: AsyncSession,
) -> None:
    """Находит предмет по имени или возвращает None, если не найден."""
    await get_or_create_subject(async_session, CHAT_ID, "Алгебра")
    await async_session.commit()

    found = await get_subject_by_name(async_session, CHAT_ID, "Алгебра")
    assert found is not None
    assert found.subject_name == "Алгебра"

    not_found = await get_subject_by_name(async_session, CHAT_ID, "Неизвестно")
    assert not_found is None


async def test_add_and_remove_subject_king(
    async_session: AsyncSession,
) -> None:
    """Проверяет добавление и удаление королей предмета."""
    subj = await get_or_create_subject(async_session, CHAT_ID, "Геометрия")
    await async_session.commit()

    # Добавляем короля
    added = await add_subject_king(async_session, subj, 12345)
    assert added is True
    assert 12345 in (subj.kings or [])

    # Повторное добавление должно вернуть False
    added_again = await add_subject_king(async_session, subj, 12345)
    assert added_again is False

    # Удаляем короля
    removed = await remove_subject_king(async_session, subj, 12345)
    assert removed is True
    assert 12345 not in (subj.kings or [])

    # Повторное удаление должно вернуть False
    removed_again = await remove_subject_king(async_session, subj, 12345)
    assert removed_again is False
