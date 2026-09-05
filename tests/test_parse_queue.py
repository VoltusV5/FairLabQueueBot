"""Тесты для модуля разбора очереди (src/utils/parse_queue.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.utils.parse_queue import (
    _expand_year,
    _parse_time_part,
    compute_default_autoclose,
    lesson_datetime_from_command,
    parse_date_time_tokens,
    parse_duration_minutes,
    parse_subject_datetime_tokens,
    split_queue_command_message,
)


def test_expand_year() -> None:
    """Проверка определения года по строковому токену."""
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    assert _expand_year(5, 9, None, now) == 2026
    assert _expand_year(5, 9, "26", now) == 2026
    assert _expand_year(5, 9, "99", now) == 2099
    assert _expand_year(5, 9, "2025", now) == 2025

    with pytest.raises(ValueError, match="должен состоять из цифр"):
        _expand_year(5, 9, "abc", now)

    with pytest.raises(ValueError, match="2- или 4-значным"):
        _expand_year(5, 9, "202", now)


def test_parse_time_part() -> None:
    """Проверка разбора строки времени на часы и минуты."""
    assert _parse_time_part("14:30") == (14, 30)
    assert _parse_time_part("14.30") == (14, 30)
    assert _parse_time_part("00:00") == (0, 0)
    assert _parse_time_part("23:59") == (23, 59)

    with pytest.raises(ValueError, match="Неверный формат"):
        _parse_time_part("14-30")

    with pytest.raises(ValueError, match="Неверный формат"):
        _parse_time_part("aa:bb")

    with pytest.raises(ValueError, match="Часы должны быть от 0 до 23"):
        _parse_time_part("24:00")

    with pytest.raises(ValueError, match="Минуты должны быть от 0 до 59"):
        _parse_time_part("12:60")


def test_parse_date_time_tokens() -> None:
    """Проверка сборки datetime из разобранных токенов."""
    now = datetime(2026, 9, 5, 12, 30, tzinfo=UTC)

    # Оба токена None -> возврат now
    assert parse_date_time_tokens(None, None, now) == now

    # Только время -> сегодня с указанным временем
    dt_time_only = parse_date_time_tokens(None, "15:45", now)
    assert dt_time_only == datetime(2026, 9, 5, 15, 45, tzinfo=UTC)

    # Только дата (день.месяц) -> текущий год и текущее время
    dt_date_only = parse_date_time_tokens("10.12", None, now)
    assert dt_date_only == datetime(2026, 12, 10, 12, 30, tzinfo=UTC)

    # Полная дата и время
    dt_full = parse_date_time_tokens("15.11.2025", "18:00", now)
    assert dt_full == datetime(2025, 11, 15, 18, 0, tzinfo=UTC)

    # Двузначный год
    dt_2digit_year = parse_date_time_tokens("15.11.26", "18.00", now)
    assert dt_2digit_year == datetime(2026, 11, 15, 18, 0, tzinfo=UTC)

    # Ошибки формата даты
    with pytest.raises(ValueError, match="Неверный формат даты"):
        parse_date_time_tokens("15", "10:00", now)

    with pytest.raises(ValueError, match="Нечисловая дата"):
        parse_date_time_tokens("ab.cd", "10:00", now)

    with pytest.raises(ValueError, match="Несуществующая дата"):
        parse_date_time_tokens("31.02.2026", "10:00", now)


def test_parse_subject_datetime_tokens() -> None:
    """Проверка выделения названия предмета, даты и времени."""
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    # Только предмет
    s, d, t = parse_subject_datetime_tokens(["Математика"], now)
    assert s == "Математика" and d is None and t is None

    # Предмет в кавычках
    s, d, t = parse_subject_datetime_tokens(['"Базы', 'данных"'], now)
    assert s == "Базы данных" and d is None and t is None

    # Предмет с датой и временем
    s, d, t = parse_subject_datetime_tokens(
        ["Физика", "лаба", "1", "12.10", "14:00"], now
    )
    assert s == "Физика лаба 1"
    assert d == "12.10"
    assert t == "14:00"

    # Предмет только со временем
    s, d, t = parse_subject_datetime_tokens(["Химия", "10.30"], now)
    assert s == "Химия" and d is None and t == "10:30"

    # Предмет только с датой
    s, d, t = parse_subject_datetime_tokens(["Химия", "10.12.2026"], now)
    assert s == "Химия" and d == "10.12.2026" and t is None

    # Ошибки
    with pytest.raises(ValueError, match="Укажите предмет"):
        parse_subject_datetime_tokens([], now)

    with pytest.raises(ValueError, match="Пустое название предмета"):
        parse_subject_datetime_tokens(['""'], now)


def test_split_queue_command_message() -> None:
    """Проверка разбора строки команды создания очереди."""
    # Обычный вызов
    s, d, t = split_queue_command_message("/queue Алгебра 20.09 10:00")
    assert s == "Алгебра" and d == "20.09" and t == "10:00"

    # Вызов с упоминанием бота
    s, d, t = split_queue_command_message(
        "/queue@FairLabQueueBot Алгебра 20.09 10:00"
    )
    assert s == "Алгебра" and d == "20.09" and t == "10:00"

    # Регистронезависимость
    s, d, t = split_queue_command_message("/QUEUE Информатика")
    assert s == "Информатика" and d is None and t is None

    # Ошибки
    with pytest.raises(ValueError, match="начинаться с /queue"):
        split_queue_command_message("/start Алгебра")

    with pytest.raises(ValueError, match="Укажите предмет"):
        split_queue_command_message("/queue")

    with pytest.raises(ValueError, match="Укажите предмет"):
        split_queue_command_message("/queue@FairLabBot   ")


def test_lesson_datetime_from_command() -> None:
    """Проверка сборки даты занятия и флага неявного времени."""
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    # Явные дата и время
    subj, dt, implicit = lesson_datetime_from_command(
        "/queue Физика 10.09 14:00", now
    )
    assert subj == "Физика"
    assert dt == datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    assert implicit is False

    # Неявные дата и время
    subj, dt, implicit = lesson_datetime_from_command("/queue Физика", now)
    assert subj == "Физика"
    assert dt == now
    assert implicit is True


def test_compute_default_autoclose() -> None:
    """Проверка вычисления стандартного времени автозакрытия набора."""
    now = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)

    # Создана менее чем за 1 час до занятия -> None
    lesson_45m = now + timedelta(minutes=45)
    assert compute_default_autoclose(now, lesson_45m) is None

    # Создана за 5 часов до занятия -> за 1 час до занятия
    lesson_5h = now + timedelta(hours=5)
    close_5h = compute_default_autoclose(now, lesson_5h)
    assert close_5h == lesson_5h - timedelta(hours=1)

    # Создана за 24 часа до занятия -> за 15 часов до занятия
    lesson_24h = now + timedelta(hours=24)
    close_24h = compute_default_autoclose(now, lesson_24h)
    assert close_24h == lesson_24h - timedelta(hours=15)

    # Занятие в прошлом -> None
    lesson_past = now - timedelta(hours=2)
    assert compute_default_autoclose(now, lesson_past) is None

    # Совместимость с naive datetime
    naive_created = datetime(2026, 9, 5, 10, 0)
    naive_lesson = datetime(2026, 9, 5, 15, 0)
    assert compute_default_autoclose(naive_created, naive_lesson) == datetime(
        2026, 9, 5, 14, 0
    )


def test_parse_duration_minutes() -> None:
    """Проверка парсинга пользовательской длительности."""
    assert parse_duration_minutes("30") == 30
    assert parse_duration_minutes("45м") == 45
    assert parse_duration_minutes("45m") == 45
    assert parse_duration_minutes("15 мин") == 15
    assert parse_duration_minutes("90 min") == 90
    assert parse_duration_minutes("60 minutes") == 60
    assert parse_duration_minutes("1 минута") == 1

    assert parse_duration_minutes("2ч") == 120
    assert parse_duration_minutes("2h") == 120
    assert parse_duration_minutes("1.5 ч") == 90
    assert parse_duration_minutes("1,5h") == 90
    assert parse_duration_minutes("3 часа") == 180
    assert parse_duration_minutes("5 часов") == 300

    assert parse_duration_minutes("") is None
    assert parse_duration_minutes("abc") is None
    assert parse_duration_minutes("-10") is None
    assert parse_duration_minutes("0") is None
    assert parse_duration_minutes("-2ч") is None
