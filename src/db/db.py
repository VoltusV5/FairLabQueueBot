"""Модуль подключения и инициализации сессий базы данных PostgreSQL.

Предоставляет асинхронный движок SQLAlchemy и генераторы сессий
для работы с БД через asyncpg.
"""

import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env")


def _make_engine() -> AsyncEngine:
    """Создает и возвращает асинхронный движок базы данных SQLAlchemy.

    Returns:
        AsyncEngine: Настроенный асинхронный движок SQLAlchemy.
    """
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        # Fallback для локальной разработки и тестирования.
        database_url = (
            "postgresql+asyncpg://postgres:postgres_password"
            "@localhost:5432/fairqueue_test"
        )
    else:
        database_url = re.sub(
            r"^postgres(?:ql)?://", "postgresql+asyncpg://", database_url
        )

    return create_async_engine(database_url, pool_pre_ping=True, echo=False)


engine = _make_engine()
SQL_URL = str(engine.url)

# Создание асинхронной локальной сессии
AsyncSessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, bind=engine, class_=AsyncSession
)


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Контекстный менеджер для получения асинхронной сессии БД.

    Yields:
        AsyncSession: Экземпляр асинхронной сессии SQLAlchemy.

    Raises:
        Exception: Откатывает транзакцию и пробрасывает возникшее исключение.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_db2() -> AsyncSession:
    """Возвращает новую сессию для ручного управления ее жизненным циклом.

    Returns:
        AsyncSession: Экземпляр асинхронной сессии SQLAlchemy.
    """
    return AsyncSessionLocal()
