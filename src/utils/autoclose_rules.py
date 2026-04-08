"""Кастомные правила автозакрытия (/newautorule) и выбор даты закрытия."""

from __future__ import annotations

from datetime import datetime, timedelta

from .parse_queue import compute_default_autoclose


def parse_newautorule_line(rules_str: str) -> list[dict]:
    """
    Формат: 0-1:n,1-18:1,18-999:15
    Интервал [min_h, max_h) в часах между созданием и занятием;
    значение после : — за сколько часов до занятия закрыть набор (n/null — не закрывать автоматически).
    """
    rules: list[dict] = []
    for raw in rules_str.replace(",", " ").split():
        part = raw.strip()
        if not part or part.lower() in ("default", "сброс"):
            continue
        if ":" not in part:
            raise ValueError(f"Неверный фрагмент: {part}")
        range_part, close_part = part.rsplit(":", 1)
        if "-" not in range_part:
            raise ValueError(f"Неверный интервал: {range_part}")
        a, b = range_part.split("-", 1)
        min_h, max_h = float(a), float(b)
        cp = close_part.strip().lower()
        if cp in ("n", "null", "none", "-"):
            close_before = None
        else:
            close_before = float(close_part.replace(",", "."))
        rules.append(
            {"min_h": min_h, "max_h": max_h, "close_before_lesson_h": close_before}
        )
    return rules


def apply_custom_autoclose_rules(
    created: datetime, lesson: datetime, rules: list[dict]
) -> datetime | None:
    delta_h = (lesson - created).total_seconds() / 3600.0
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
    custom_rules: list[dict] | None,
) -> datetime | None:
    if not autoclose_enabled:
        return None
    if custom_rules:
        return apply_custom_autoclose_rules(created, lesson, custom_rules)
    return compute_default_autoclose(created, lesson)


def group_users_together(order: list[int], group_ids: list[int]) -> list[int]:
    """Поставить group_ids подряд в порядке упоминаний, начиная с минимальной позиции."""
    s = set(group_ids)
    if len(s) != len(group_ids):
        raise ValueError("Повтор в списке")
    for uid in group_ids:
        if uid not in order:
            raise ValueError("Не все пользователи в очереди")
    insert_at = min(order.index(uid) for uid in group_ids)
    cnt = sum(1 for j in range(insert_at) if order[j] not in s)
    others = [x for x in order if x not in s]
    return others[:cnt] + list(group_ids) + others[cnt:]
