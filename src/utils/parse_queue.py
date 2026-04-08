"""Разбор даты/времени и текста команды /queue."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_DATE_TOKEN = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{2,4})?$")
_TIME_TOKEN = re.compile(r"^\d{1,2}[:.]\d{2}$")


def _expand_year(day: int, month: int, year_part: str | None, now: datetime) -> int:
    if year_part is None:
        return now.year
    y = year_part.strip()
    if len(y) == 2:
        y_int = int(y)
        return 2000 + y_int if y_int < 100 else y_int
    return int(y)


def parse_date_time_tokens(
    date_token: str | None, time_token: str | None, now: datetime
) -> datetime:
    """Собрать datetime из токенов. Правила из ТЗ."""
    if date_token is None and time_token is None:
        return now

    if date_token is None:
        # только время — сегодня (дата now)
        h, m = _parse_time_part(time_token or "00:00")
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    parts = date_token.split(".")
    day, month = int(parts[0]), int(parts[1])
    year: int
    if len(parts) == 2:
        year = now.year
    else:
        year = _expand_year(day, month, parts[2], now)

    if time_token is None:
        h, m = now.hour, now.minute
    else:
        h, m = _parse_time_part(time_token)

    return datetime(year, month, day, h, m, 0, 0)


def _parse_time_part(token: str) -> tuple[int, int]:
    token = token.replace(".", ":")
    a, b = token.split(":", 1)
    return int(a), int(b)


def parse_subject_datetime_tokens(
    parts: list[str], now: datetime | None = None
) -> tuple[str, str | None, str | None]:
    """Из токенов после команды: предмет, опционально дата и время."""
    if now is None:
        now = datetime.utcnow()
    if not parts:
        raise ValueError("Укажите предмет")

    time_tok: str | None = None
    date_tok: str | None = None
    work = list(parts)

    if len(work) >= 1 and _TIME_TOKEN.match(work[-1]):
        time_tok = work[-1].replace(".", ":")
        work = work[:-1]

    if len(work) >= 1 and _DATE_TOKEN.match(work[-1]):
        date_tok = work[-1]
        work = work[:-1]

    subject_raw = " ".join(work).strip()
    if not subject_raw:
        raise ValueError("Укажите название предмета")

    if (subject_raw.startswith('"') and subject_raw.endswith('"')) or (
        subject_raw.startswith("'") and subject_raw.endswith("'")
    ):
        subject_name = subject_raw[1:-1].strip()
    else:
        subject_name = subject_raw

    if not subject_name:
        raise ValueError("Пустое название предмета")

    return subject_name, date_tok, time_tok


def split_queue_command_message(user_command: str, now: datetime | None = None) -> tuple[str, str | None, str | None]:
    """
    Возвращает (subject_name, date_token|None, time_token|None).
    subject — без кавычек, как ввод пользователя (можно в кавычках).
    """
    if now is None:
        now = datetime.utcnow()
    text = user_command.strip()
    if not text.lower().startswith("/queue"):
        raise ValueError("Команда должна начинаться с /queue")
    rest = text[6:].strip()
    if not rest:
        raise ValueError("Укажите предмет")
    parts = rest.split()
    return parse_subject_datetime_tokens(parts, now)


def lesson_datetime_from_command(
    user_command: str, now: datetime | None = None
) -> tuple[str, datetime, bool]:
    """(subject_name, lesson_datetime, implicit_now).

    implicit_now=True — дата и время в команде не заданы; занятие привязано к моменту создания,
    автозакрытие по дельте не применяется.
    """
    if now is None:
        now = datetime.utcnow()
    subj, d, t = split_queue_command_message(user_command, now)
    implicit = d is None and t is None
    dt = parse_date_time_tokens(d, t, now)
    return subj, dt, implicit


def compute_default_autoclose(
    created: datetime, lesson: datetime
) -> datetime | None:
    """
    Если очередь создана < 1 ч до занятия — автозакрытия нет (None).
    1–18 ч до занятия — закрытие за 1 ч до занятия.
    >= 18 ч — за 15 ч до занятия.
    """
    delta = lesson - created
    if delta < timedelta(hours=1):
        return None
    if delta <= timedelta(hours=18):
        return lesson - timedelta(hours=1)
    return lesson - timedelta(hours=15)


def parse_duration_minutes(text: str) -> int | None:
    """'30', '30м', '2ч', '1h', '90 min' → минуты."""
    s = text.strip().lower().replace(" ", "")
    if not s:
        return None
    if s.endswith("ч") or s.endswith("h"):
        num = float(s[:-1].replace(",", "."))
        return int(num * 60)
    if s.endswith("м") or s.endswith("m"):
        return int(s[:-1])
    if s.isdigit():
        return int(s)
    return None
