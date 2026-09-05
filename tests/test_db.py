"""Тесты инфраструктурного слоя базы данных (db.py)."""

import os
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import text
from src.db.db import _make_engine, get_db
from src.db.init_db import User


def test_make_engine_fallback():
    """Проверяет генерацию URL по умолчанию при отсутствии DATABASE_URL."""
    with patch.dict(os.environ, {"DATABASE_URL": ""}):
        engine = _make_engine()
        assert "postgresql+asyncpg://" in str(engine.url)
        assert "fairqueue_test" in str(engine.url)


@pytest.mark.parametrize(
    "raw_url",
    [
        "postgres://user:pass@localhost/db",
        "postgresql://user:pass@localhost/db",
    ],
)
def test_make_engine_url_conversion(raw_url: str):
    """Проверяет автозамену диалектов postgres:// и postgresql://."""
    with patch.dict(os.environ, {"DATABASE_URL": raw_url}):
        engine = _make_engine()
        assert str(engine.url).startswith("postgresql+asyncpg://")


@pytest.mark.asyncio
async def test_get_db_context_manager_and_rollback(async_engine):
    """Проверяет создание сессии через get_db и откат при исключении."""
    mock_sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, class_=AsyncSession
    )

    with patch("src.db.db.AsyncSessionLocal", mock_sessionmaker):
        # 1. Успешное выполнение в get_db
        async with get_db() as session:
            assert isinstance(session, AsyncSession)
            user = User(tg_id=99999, tg_username="test_user")
            session.add(user)
            await session.commit()

        # 2. Проверяем rollback при исключении
        with pytest.raises(ValueError):
            async with get_db() as session:
                user2 = User(tg_id=88888, tg_username="rollback_user")
                session.add(user2)
                await session.flush()
                raise ValueError("Тестовая ошибка для проверки rollback")

        # 3. Убеждаемся, что user2 не попал в БД из-за rollback
        async with get_db() as session:
            result = await session.execute(
                text("SELECT count(*) FROM users WHERE tg_id = 88888")
            )
            count = result.scalar()
            assert count == 0
