"""Кастомные правила автозакрытия очереди (/newautorule) и группы участников.

Модуль предоставляет функции разбора пользовательских правил автозакрытия,
применения их к моментам создания и проведения занятия, а также алгоритм
размещения участников постоянной группы подряд в сформированной очереди.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from .parse_queue import compute_default_autoclose

__all__ = [
    "AutocloseRule",
    "apply_custom_autoclose_rules",
    "compute_queue_close_at",
    "group_users_together",
    "parse_newautorule_line",
]

_RANGE_RE = re.compile(r"^([+-]?\d+(?:[.,]\d+)?)-([+-]?\d+(?:[.,]\d+)?)$")


class AutocloseRule(TypedDict):
    """Структура интервального правила автозакрытия очереди.

    Attributes:
        min_h: Левая граница интервала между созданием и занятием (в часах).
        max_h: Правая граница интервала (в часах, не включая границу).
        close_before_lesson_h: За сколько часов до занятия закрыть набор
            (None означает отсутствие автозакрытия для данного интервала).
    """

    min_h: float
    max_h: float
    close_before_lesson_h: float | None


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


def parse_newautorule_line(rules_str: str) -> list[AutocloseRule]:
    """Разобрать строку пользовательских правил автозакрытия.

    Формат строки: '0-1:n,1-18:1,18-999:15'
    Интервал [min_h, max_h) в часах между созданием очереди и занятием;
    значение после двоеточия — за сколько часов до занятия закрыть набор.
    Значения 'n', 'null', 'none', '-' отключают автозакрытие для интервала.
    Ключевые слова 'default' и 'сброс' игнорируются при парсинге.

    Args:
        rules_str: Исходная строка с правилами.

    Returns:
        Список структурированных правил AutocloseRule.

    Raises:
        ValueError: При нарушении синтаксиса или логических границ интервалов.
    """
    rules: list[AutocloseRule] = []
    for raw in rules_str.replace(",", " ").split():
        part = raw.strip()
        if not part or part.lower() in ("default", "сброс"):
            continue
        if ":" not in part:
            raise ValueError(
                f"Неверный фрагмент правила (отсутствует разделитель): {part}"
            )

        range_part, close_part = part.rsplit(":", 1)
        match = _RANGE_RE.match(range_part)
        if not match:
            if "-" not in range_part:
                raise ValueError(
                    f"Неверный формат интервала (нет '-'): {range_part}"
                )
            raise ValueError(
                f"Границы интервала должны быть числами: {range_part}"
            )

        min_h = float(match.group(1).replace(",", "."))
        max_h = float(match.group(2).replace(",", "."))

        if min_h < 0 or max_h < 0:
            raise ValueError("Границы интервала не могут быть отрицательными")
        if min_h >= max_h:
            raise ValueError(
                f"Левая граница ({min_h}) должна быть меньше правой ({max_h})"
            )

        cp = close_part.strip().lower()
        if cp in ("n", "null", "none", "-"):
            close_before: float | None = None
        else:
            try:
                close_before = float(close_part.replace(",", "."))
            except ValueError as exc:
                raise ValueError(
                    f"Некорректное значение времени закрытия: {close_part}"
                ) from exc
            if close_before < 0:
                raise ValueError(
                    "Время закрытия до занятия не может быть отрицательным"
                )

        rules.append(
            {
                "min_h": min_h,
                "max_h": max_h,
                "close_before_lesson_h": close_before,
            }
        )
    return rules


def apply_custom_autoclose_rules(
    created: datetime,
    lesson: datetime,
    rules: Sequence[AutocloseRule | dict[str, Any]],
) -> datetime | None:
    """Применить кастомные правила автозакрытия для заданной пары дат.

    Args:
        created: Момент создания очереди.
        lesson: Момент проведения занятия.
        rules: Список кастомных правил.

    Returns:
        Вычисленный момент времени закрытия набора либо None,
        если для текущего интервала автозакрытие отключено или не найдено.
    """
    c_dt = _ensure_utc(created)
    l_dt = _ensure_utc(lesson)
    delta_h = (l_dt - c_dt).total_seconds() / 3600.0

    if delta_h <= 0:
        return None

    for r in rules:
        if r["min_h"] <= delta_h < r["max_h"]:
            cb = r["close_before_lesson_h"]
            if cb is None:
                return None
            return lesson - timedelta(hours=float(cb))
    return None


def compute_queue_close_at(
    created: datetime,
    lesson: datetime,
    *,
    autoclose_enabled: bool,
    custom_rules: Sequence[AutocloseRule | dict[str, Any]] | None,
) -> datetime | None:
    """Вычислить момент времени закрытия набора в очередь.

    Если автозакрытие выключено для чата, возвращает None.
    Если заданы кастомные правила, применяет их. В противном случае
    использует стандартные интервалы автозакрытия.

    Args:
        created: Момент создания очереди.
        lesson: Момент проведения занятия.
        autoclose_enabled: Флаг активности автозакрытия в чате.
        custom_rules: Пользовательские правила чата или None.

    Returns:
        Момент времени закрытия набора либо None.
    """
    if not autoclose_enabled:
        return None
    if custom_rules:
        return apply_custom_autoclose_rules(created, lesson, custom_rules)
    return compute_default_autoclose(created, lesson)


def group_users_together(order: list[int], group_ids: list[int]) -> list[int]:
    """Сгруппировать указанных пользователей подряд в сформированной очереди.

    Участники group_ids размещаются подряд в порядке их следования в
    group_ids, начиная с минимальной позиции, которую занимал любой из
    них в исходном списке.

    Args:
        order: Исходный порядок ID пользователей в очереди.
        group_ids: Список ID пользователей, входящих в группу.

    Returns:
        Новый список ID пользователей с объединенной группой.

    Raises:
        ValueError: При обнаружении дубликатов в group_ids или если
            кто-либо из участников группы отсутствует в очереди.
    """
    if not group_ids:
        return list(order)

    s = set(group_ids)
    if len(s) != len(group_ids):
        raise ValueError("Повтор в списке участников группы")

    for uid in group_ids:
        if uid not in order:
            raise ValueError(f"Пользователь {uid} отсутствует в очереди")

    insert_at = min(order.index(uid) for uid in group_ids)
    cnt = sum(1 for j in range(insert_at) if order[j] not in s)
    others = [x for x in order if x not in s]
    return others[:cnt] + list(group_ids) + others[cnt:]
