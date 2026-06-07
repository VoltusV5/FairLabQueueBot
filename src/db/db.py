"""Файл для подключения БД
Импортируй этот файл и у тебя будет доступ к БД.

Если задан DATABASE_URL (PostgreSQL), используется он; иначе — SQLite app.db.
"""

import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .init_db import Base

# Загрузка .env из корня проекта до чтения DATABASE_URL
_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env")


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
                    "ALTER TABLE chats ADD COLUMN autoclose_enabled INTEGER NOT NULL DEFAULT 0"
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

def _make_engine():
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = "postgresql+psycopg2://" + database_url[
                len("postgres://") :
            ]
        elif database_url.startswith("postgresql://"):
            database_url = "postgresql+psycopg2://" + database_url[
                len("postgresql://") :
            ]
        return create_engine(database_url, pool_pre_ping=True)
    db_path = Path(__file__).resolve().parent.parent / "app.db"
    sql_url = f"sqlite:///{db_path}"
    return create_engine(sql_url, connect_args={"check_same_thread": False})


engine = _make_engine()
SQL_URL = str(engine.url)

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