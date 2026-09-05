"""Тесты для модуля правил автозакрытия (src/utils/autoclose_rules.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.utils.autoclose_rules import (
    AutocloseRule,
    apply_custom_autoclose_rules,
    compute_queue_close_at,
    group_users_together,
    parse_newautorule_line,
)


def test_parse_newautorule_line_valid() -> None:
    """Проверка успешного разбора строки кастомных правил автозакрытия."""
    line = "0-1:n,1-18:1,18-999:15"
    rules = parse_newautorule_line(line)
    assert len(rules) == 3
    assert rules[0] == {
        "min_h": 0.0,
        "max_h": 1.0,
        "close_before_lesson_h": None,
    }
    assert rules[1] == {
        "min_h": 1.0,
        "max_h": 18.0,
        "close_before_lesson_h": 1.0,
    }
    assert rules[2] == {
        "min_h": 18.0,
        "max_h": 999.0,
        "close_before_lesson_h": 15.0,
    }

    # Проверка альтернативных значений отключения: null, none, -
    alt_line = "0-2:null 2-10:1.5 10-24:none 24-100:-"
    alt_rules = parse_newautorule_line(alt_line)
    assert len(alt_rules) == 4
    assert alt_rules[0]["close_before_lesson_h"] is None
    assert alt_rules[1]["close_before_lesson_h"] == 1.5
    assert alt_rules[2]["close_before_lesson_h"] is None
    assert alt_rules[3]["close_before_lesson_h"] is None

    # Игнорирование ключевых слов default и сброс
    kw_rules = parse_newautorule_line("default 1-10:2 сброс")
    assert len(kw_rules) == 1
    assert kw_rules[0]["min_h"] == 1.0

    # Пустая строка
    assert parse_newautorule_line("") == []
    assert parse_newautorule_line("   ") == []


def test_parse_newautorule_line_errors() -> None:
    """Проверка обработки некорректных строк правил автозакрытия."""
    with pytest.raises(ValueError, match="отсутствует разделитель"):
        parse_newautorule_line("1-18")

    with pytest.raises(ValueError, match="нет '-'"):
        parse_newautorule_line("1:18")

    with pytest.raises(ValueError, match="должны быть числами"):
        parse_newautorule_line("a-b:1")

    with pytest.raises(ValueError, match="Некорректное значение времени"):
        parse_newautorule_line("1-18:abc")

    with pytest.raises(ValueError, match="не могут быть отрицательными"):
        parse_newautorule_line("-1-18:1")

    with pytest.raises(ValueError, match="должна быть меньше правой"):
        parse_newautorule_line("18-1:1")

    with pytest.raises(ValueError, match="должна быть меньше правой"):
        parse_newautorule_line("5-5:1")

    with pytest.raises(ValueError, match="не может быть отрицательным"):
        parse_newautorule_line("1-18:-2")


def test_apply_custom_autoclose_rules() -> None:
    """Проверка применения кастомных правил к интервалу времени."""
    now = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    rules: list[AutocloseRule] = [
        {"min_h": 0.0, "max_h": 2.0, "close_before_lesson_h": None},
        {"min_h": 2.0, "max_h": 10.0, "close_before_lesson_h": 1.0},
        {"min_h": 10.0, "max_h": 48.0, "close_before_lesson_h": 12.0},
    ]

    # Интервал 5 часов -> попадает во второе правило (за 1 час)
    lesson_5h = now + timedelta(hours=5)
    close_5h = apply_custom_autoclose_rules(now, lesson_5h, rules)
    assert close_5h == lesson_5h - timedelta(hours=1)

    # Интервал 1 час -> попадает в первое правило (None)
    lesson_1h = now + timedelta(hours=1)
    assert apply_custom_autoclose_rules(now, lesson_1h, rules) is None

    # Интервал 60 часов -> не покрыт правилами -> None
    lesson_60h = now + timedelta(hours=60)
    assert apply_custom_autoclose_rules(now, lesson_60h, rules) is None

    # Занятие в прошлом -> None
    lesson_past = now - timedelta(hours=1)
    assert apply_custom_autoclose_rules(now, lesson_past, rules) is None

    # Поддержка naive datetime
    naive_now = datetime(2026, 9, 5, 10, 0)
    naive_lesson = datetime(2026, 9, 5, 15, 0)
    close_naive = apply_custom_autoclose_rules(naive_now, naive_lesson, rules)
    assert close_naive == datetime(2026, 9, 5, 14, 0)


def test_compute_queue_close_at() -> None:
    """Проверка комплексного вычисления дедлайна закрытия очереди."""
    created = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
    lesson = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)
    custom_rules: list[AutocloseRule] = [
        {"min_h": 1.0, "max_h": 10.0, "close_before_lesson_h": 2.0}
    ]

    # Автозакрытие выключено для чата
    assert (
        compute_queue_close_at(
            created,
            lesson,
            autoclose_enabled=False,
            custom_rules=custom_rules,
        )
        is None
    )

    # Кастомные правила активны
    close_custom = compute_queue_close_at(
        created,
        lesson,
        autoclose_enabled=True,
        custom_rules=custom_rules,
    )
    assert close_custom == lesson - timedelta(hours=2)

    # Кастомных правил нет -> дефолтный расчет (за 1 час)
    close_default = compute_queue_close_at(
        created,
        lesson,
        autoclose_enabled=True,
        custom_rules=None,
    )
    assert close_default == lesson - timedelta(hours=1)


def test_group_users_together() -> None:
    """Проверка алгоритма группировки участников подряд."""
    order = [10, 20, 30, 40, 50, 60]

    # Группа из 40 и 20: минимальная позиция 20 (индекс 1).
    # Порядок группы должен быть [40, 20].
    # Оставшиеся: 10, 30, 50, 60.
    # Результат: [10, 40, 20, 30, 50, 60].
    res = group_users_together(order, [40, 20])
    assert res == [10, 40, 20, 30, 50, 60]

    # Пустая группа -> возвращает копию очереди
    assert group_users_together(order, []) == order

    # Группа из одного участника -> очередь без изменений
    assert group_users_together(order, [30]) == order

    # Повтор в group_ids
    with pytest.raises(ValueError, match="Повтор в списке"):
        group_users_together(order, [20, 20])

    # Участник отсутствует в очереди
    with pytest.raises(ValueError, match="отсутствует в очереди"):
        group_users_together(order, [20, 999])
