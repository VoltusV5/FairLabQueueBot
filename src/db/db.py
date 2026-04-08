"""Файл для подключения БД
Импортируй этот файл и у тебя будет доступ к БД.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
from .init_db import Base
from contextlib import contextmanager


def _migrate_sqlite_schema(engine) -> None:
    """Добавляет колонки в существующие таблицы (create_all не обновляет схему)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        r = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chats' LIMIT 1"
            )
        )
        if not r.fetchone():
            return
        r = conn.execute(text("PRAGMA table_info(chats)"))
        cols = {row[1] for row in r.fetchall()}
        if "autoclose_enabled" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN autoclose_enabled INTEGER NOT NULL DEFAULT 1"
                )
            )
        if "autoclose_rules" not in cols:
            conn.execute(text("ALTER TABLE chats ADD COLUMN autoclose_rules TEXT"))
        if "subscription_reminder_state" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN subscription_reminder_state TEXT"
                )
            )
        if "groups" not in cols:
            conn.execute(text("ALTER TABLE chats ADD COLUMN groups TEXT"))

# Путь к базе данных относительно текущего файла (db.py)
db_path = Path(__file__).resolve().parent.parent / 'app.db'

# Строка подключения с использованием вычисленного пути
SQL_URL = f"sqlite:///{db_path}"

# Создаем движок для подключения к базе данных
engine = create_engine(SQL_URL, connect_args={"check_same_thread": False})

# Создание локальной сессии
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Создание таблиц, если их еще нет
Base.metadata.create_all(bind=engine)
_migrate_sqlite_schema(engine)


# Интерфейс для инициализации БД в контекст менеджере
@contextmanager
def get_db() -> Session:
    """Интерфейс для инициализации БД в контекст менеджере"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db2() -> Session:
    return SessionLocal()