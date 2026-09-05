"""Тесты для сервиса проверки подписок (src/services/subscription.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.db.init_db import Chat
from src.services.subscription import (
    current_access_deadline,
    effective_access,
    has_base_features,
    has_supervip,
)


def _make_chat(
    tier: str | None = None,
    sub_end: datetime | None = None,
    trial_end: datetime | None = None,
) -> Chat:
    """Вспомогательная фабрика для создания модели Chat в тестах."""
    return Chat(
        chat_id=123,
        title="Тестовый чат",
        subscription_tier=tier,
        subscription_ends_at=sub_end,
        trial_ends_at=trial_end,
        autoclose_enabled=False,
    )


def test_effective_access_none() -> None:
    """Проверка обработки None-объекта чата."""
    assert effective_access(None) == "expired"
    assert current_access_deadline(None) is None


def test_effective_access_active_trial() -> None:
    """Проверка статуса доступа при действующем пробном периоде."""
    now = datetime.now(UTC)
    trial_end = now + timedelta(days=7)
    chat = _make_chat(trial_end=trial_end)

    access = effective_access(chat, now)
    assert access == "base"
    assert has_base_features(access) is True
    assert has_supervip(access) is False
    deadline = current_access_deadline(chat, now)
    assert deadline is not None
    assert deadline == trial_end


def test_effective_access_expired_trial() -> None:
    """Проверка статуса доступа при истекшем пробном периоде."""
    now = datetime.now(UTC)
    trial_end = now - timedelta(days=1)
    chat = _make_chat(trial_end=trial_end)

    access = effective_access(chat, now)
    assert access == "expired"
    assert has_base_features(access) is False
    assert has_supervip(access) is False
    assert current_access_deadline(chat, now) is None


def test_effective_access_base_tier() -> None:
    """Проверка активного и истекшего тарифа Base."""
    now = datetime.now(UTC)
    sub_end = now + timedelta(days=15)
    chat = _make_chat(tier="base", sub_end=sub_end)

    access = effective_access(chat, now)
    assert access == "base"
    assert has_base_features(access) is True
    assert has_supervip(access) is False
    assert current_access_deadline(chat, now) == sub_end

    # Истекший тариф Base
    chat_expired = _make_chat(tier="base", sub_end=now - timedelta(days=1))
    access_expired = effective_access(chat_expired, now)
    assert access_expired == "expired"
    assert current_access_deadline(chat_expired, now) is None


def test_effective_access_supervip_tier() -> None:
    """Проверка активного тарифа SuperVIP."""
    now = datetime.now(UTC)
    sub_end = now + timedelta(days=30)
    chat = _make_chat(tier="supervip", sub_end=sub_end)

    access = effective_access(chat, now)
    assert access == "supervip"
    assert has_base_features(access) is True
    assert has_supervip(access) is True
    assert current_access_deadline(chat, now) == sub_end


def test_effective_access_supervip_fallback_to_trial() -> None:
    """Проверка отката на Base/Trial при истекшей SuperVIP подписке."""
    now = datetime.now(UTC)
    chat = _make_chat(
        tier="supervip",
        sub_end=now - timedelta(days=1),
        trial_end=now + timedelta(days=5),
    )

    access = effective_access(chat, now)
    assert access == "base"
    assert has_base_features(access) is True
    assert has_supervip(access) is False
    assert current_access_deadline(chat, now) == chat.trial_ends_at


def test_has_features_predicates() -> None:
    """Проверка всех комбинаций предикатов has_base_features и has_supervip."""
    assert has_base_features("supervip") is True
    assert has_base_features("base") is True
    assert has_base_features("expired") is False

    assert has_supervip("supervip") is True
    assert has_supervip("base") is False
    assert has_supervip("expired") is False


def test_naive_and_aware_datetimes_compatibility() -> None:
    """Проверка совместимости наивных (naive) и aware datetime объектов."""
    now_aware = datetime.now(UTC)
    naive_future = datetime(2035, 1, 1, 12, 0)
    chat_naive = _make_chat(tier="base", sub_end=naive_future)

    # naive дата из БД и aware точка отсчета
    access = effective_access(chat_naive, now_aware)
    assert access == "base"
    deadline = current_access_deadline(chat_naive, now_aware)
    assert deadline is not None
    assert deadline == naive_future.replace(tzinfo=UTC)

    # aware дата из БД и naive точка отсчета
    now_naive = datetime(2026, 1, 1, 0, 0)
    aware_future = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    chat_aware = _make_chat(tier="supervip", sub_end=aware_future)

    access_2 = effective_access(chat_aware, now_naive)
    assert access_2 == "supervip"
    assert current_access_deadline(chat_aware, now_naive) == aware_future


def test_case_insensitive_tier_string() -> None:
    """Проверка регистронезависимости названий тарифов."""
    now = datetime.now(UTC)
    chat_upper = _make_chat(tier="SUPERVIP", sub_end=now + timedelta(days=10))
    assert effective_access(chat_upper, now) == "supervip"

    chat_title = _make_chat(tier="Base", sub_end=now + timedelta(days=10))
    assert effective_access(chat_title, now) == "base"
