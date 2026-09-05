"""Разбор даты, времени и текста команды создания очереди /queue.

Модуль предоставляет сервисные функции для парсинга параметров команды
/queue (название предмета, дата, время занятия), расчета стандартного
интервала автозакрытия и парсинга пользовательской длительности.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

__all__ = [
    "compute_default_autoclose",
    "lesson_datetime_from_command",
    "parse_date_time_tokens",
    "parse_duration_minutes",
    "parse_subject_datetime_tokens",
    "split_queue_command_message",
]

_DATE_TOKEN = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{2,4})?$")
_TIME_TOKEN = re.compile(r"^\d{1,2}[:.]\d{2}$")


def _ensure_utc(dt: datetime) -> datetime:
    """Привести datetime к объекту с временной зоной UTC.

    Args:
        dt: Исходная дата со временем.

    Returns:
        Объект datetime с tzinfo=timezone.utc.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _expand_year(
    day: int, month: int, year_part: str | None, now: datetime
) -> int:
    """Определить 4-значный год на основе переданной части и текущей даты.

    Args:
        day: День месяца.
        month: Номер месяца.
        year_part: Строка с годом или None.
        now: Текущий момент времени.

    Returns:
        Четырехзначный номер года.

    Raises:
        ValueError: Если формат года некорректен.
    """
    if year_part is None:
        return now.year
    y = year_part.strip()
    if not y.isdigit():
        raise ValueError(f"Год должен состоять из цифр: {year_part}")
    if len(y) == 2:
        y_int = int(y)
        return 2000 + y_int if y_int < 100 else y_int
    if len(y) == 4:
        return int(y)
    raise ValueError(
        f"Год должен быть 2- или 4-значным числом, получено: {year_part}"
    )


def _parse_time_part(token: str) -> tuple[int, int]:
    """Разобрать строку времени вида ЧЧ:ММ или ЧЧ.ММ на часы и минуты.

    Args:
        token: Строка времени.

    Returns:
        Кортеж (часы, минуты).

    Raises:
        ValueError: Если формат времени неверен или значения вне диапазона.
    """
    token_clean = token.replace(".", ":")
    if ":" not in token_clean:
        raise ValueError(f"Неверный формат времени: {token}")
    a, b = token_clean.split(":", 1)
    try:
        h, m = int(a), int(b)
    except ValueError as exc:
        raise ValueError(f"Неверный формат времени: {token}") from exc
    if not (0 <= h <= 23):
        raise ValueError(f"Часы должны быть от 0 до 23, получено: {h}")
    if not (0 <= m <= 59):
        raise ValueError(f"Минуты должны быть от 0 до 59, получено: {m}")
    return h, m


def parse_date_time_tokens(
    date_token: str | None, time_token: str | None, now: datetime
) -> datetime:
    """Собрать объект datetime из разобранных токенов даты и времени.

    Args:
        date_token: Строка даты (например, '25.10' или '25.10.2026') или None.
        time_token: Строка времени (например, '14:30' или '14.30') или None.
        now: Текущая дата и время для подстановки значений по умолчанию.

    Returns:
        Сконструированный объект datetime с сохранением tzinfo из now.

    Raises:
        ValueError: Если дата или время содержат недопустимые значения.
    """
    if date_token is None and time_token is None:
        return now

    if date_token is None:
        h, m = _parse_time_part(time_token or "00:00")
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    parts = date_token.split(".")
    if len(parts) < 2:
        raise ValueError(f"Неверный формат даты: {date_token}")
    try:
        day, month = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"Нечисловая дата: {date_token}") from exc

    if len(parts) == 2:
        year = now.year
    else:
        year = _expand_year(day, month, parts[2], now)

    if time_token is None:
        h, m = now.hour, now.minute
    else:
        h, m = _parse_time_part(time_token)

    try:
        return datetime(year, month, day, h, m, 0, 0, tzinfo=now.tzinfo)
    except ValueError as exc:
        dt_str = f"{day:02d}.{month:02d}.{year:04d}"
        raise ValueError(f"Несуществующая дата: {dt_str}") from exc


def parse_subject_datetime_tokens(
    parts: list[str], now: datetime | None = None
) -> tuple[str, str | None, str | None]:
    """Извлечь название предмета, дату и время из списка аргументов команды.

    Args:
        parts: Список переданных аргументов.
        now: Опорное текущее время (по умолчанию aware UTC).

    Returns:
        Кортеж (название_предмета, токен_даты, токен_времени).

    Raises:
        ValueError: Если список аргументов пуст или название не указано.
    """
    if now is None:
        now = datetime.now(UTC)
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


def split_queue_command_message(
    user_command: str, now: datetime | None = None
) -> tuple[str, str | None, str | None]:
    """Разобрать полный текст команды /queue на составляющие.

    Поддерживает варианты вызова как в личных сообщениях (/queue ...),
    так и в групповых чатах с упоминанием бота (/queue@bot_name ...).

    Args:
        user_command: Строка команды пользователя.
        now: Опорное текущее время.

    Returns:
        Кортеж (название_предмета, токен_даты, токен_времени).

    Raises:
        ValueError: Если команда имеет неверный префикс или аргументы пусты.
    """
    if now is None:
        now = datetime.now(UTC)
    text = user_command.strip()

    match = re.match(
        r"^/queue(?:@\w+)?(?:\s+(.*))?$", text, flags=re.IGNORECASE | re.DOTALL
    )
    if not match:
        raise ValueError("Команда должна начинаться с /queue")

    rest = (match.group(1) or "").strip()
    if not rest:
        raise ValueError("Укажите предмет")

    parts = rest.split()
    return parse_subject_datetime_tokens(parts, now)


def lesson_datetime_from_command(
    user_command: str, now: datetime | None = None
) -> tuple[str, datetime, bool]:
    """Сформировать предмет, дату занятия и признак неявного времени.

    Args:
        user_command: Исходный текст команды создания очереди.
        now: Опорное текущее время.

    Returns:
        Кортеж (предмет, дата_занятия, неявное_время).
        Если дата и время не были указаны, неявное_время равно True.
    """
    if now is None:
        now = datetime.now(UTC)
    subj, d, t = split_queue_command_message(user_command, now)
    implicit = d is None and t is None
    dt = parse_date_time_tokens(d, t, now)
    return subj, dt, implicit


def compute_default_autoclose(
    created: datetime, lesson: datetime
) -> datetime | None:
    """Рассчитать стандартный момент времени автозакрытия набора в очередь.

    Правила:
    - Создана менее чем за 1 час до занятия — автозакрытия нет (None);
    - От 1 до 18 часов до занятия — закрытие ровно за 1 час до занятия;
    - 18 и более часов до занятия — закрытие за 15 часов до занятия.

    Args:
        created: Момент создания очереди.
        lesson: Момент начала занятия.

    Returns:
        Момент времени автозакрытия или None, если автозакрытие отключено.
    """
    c_dt = _ensure_utc(created)
    l_dt = _ensure_utc(lesson)
    delta = l_dt - c_dt

    if delta < timedelta(hours=1):
        return None
    if delta <= timedelta(hours=18):
        return lesson - timedelta(hours=1)
    return lesson - timedelta(hours=15)


def parse_duration_minutes(text: str) -> int | None:
    """Разобрать строку длительности в количество минут.

    Поддерживает различные форматы:
    - '30', '30м', '30 мин', '30m', '90 min', '45 minutes' -> минуты;
    - '2ч', '2h', '1.5 ч', '1.5 hours', '3 часа' -> перевод в минуты.

    Args:
        text: Строка с указанием длительности.

    Returns:
        Положительное количество минут или None при невозможности разбора.
    """
    s = text.strip().lower().replace(" ", "")
    if not s:
        return None

    hour_suffixes = (
        "часов",
        "часа",
        "час",
        "hours",
        "hour",
        "hrs",
        "hr",
        "ч",
        "h",
    )
    for suffix in hour_suffixes:
        if s.endswith(suffix):
            num_str = s[: -len(suffix)].replace(",", ".")
            try:
                val = float(num_str)
                return int(val * 60) if val > 0 else None
            except ValueError:
                return None

    min_suffixes = (
        "минуты",
        "минута",
        "минут",
        "minutes",
        "minute",
        "mins",
        "min",
        "мин",
        "м",
        "m",
    )
    for suffix in min_suffixes:
        if s.endswith(suffix):
            num_str = s[: -len(suffix)].replace(",", ".")
            try:
                val = float(num_str)
                return int(val) if val > 0 else None
            except ValueError:
                return None

    if s.isdigit():
        val = int(s)
        return val if val > 0 else None

    return None
