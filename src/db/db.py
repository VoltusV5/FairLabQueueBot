"""Файл для подключения БД
Импортируй этот файл и у тебя будет доступ к БД.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
from .init_db import Base
from contextlib import contextmanager

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


# Интерфейс для инициализации БД в контекст менеджере
@contextmanager
def get_db() -> Session:
    """Интерфейс для инициализации БД в контекст менеджере"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
