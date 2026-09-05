"""Сервис проверки подписки чата: trial / base / supervip."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from src.db.init_db import Chat

Access = Literal["supervip", "base", "expired"]

__all__ = [
    "Access",
    "effective_access",
    "has_base_features",
    "has_supervip",
    "current_access_deadline",
]


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Приводит datetime к часовому поясу UTC.

    Args:
        dt: Дата и время или None.

    Returns:
        datetime | None: Объект в часовом поясе UTC или None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def effective_access(chat: Chat | None, now: datetime | None = None) -> Access:
    """Определяет действующий уровень доступа чата.

    Базовые функции доступны при trial/base/supervip;
    supervip доступен только при действующей подписке supervip.

    Args:
        chat: Объект чата из БД или None.
        now: Текущие дата и время (по умолчанию datetime.now(timezone.utc)).

    Returns:
        Access: 'supervip', 'base' или 'expired'.
    """
    if chat is None:
        return "expired"
    if now is None:
        now = datetime.now(UTC)

    now_utc = _ensure_utc(now)
    sub_end = _ensure_utc(chat.subscription_ends_at)
    trial_end = _ensure_utc(chat.trial_ends_at)
    tier = (chat.subscription_tier or "trial").lower()

    if tier == "supervip" and sub_end and now_utc and sub_end > now_utc:
        return "supervip"
    if tier == "base" and sub_end and now_utc and sub_end > now_utc:
        return "base"
    if trial_end and now_utc and trial_end > now_utc:
        return "base"
    return "expired"


def has_base_features(access: Access) -> bool:
    """Проверяет наличие базовых функций у чата.

    Args:
        access: Текущий статус доступа ('supervip', 'base', 'expired').

    Returns:
        bool: True, если доступ активен, иначе False.
    """
    return access != "expired"


def has_supervip(access: Access) -> bool:
    """Проверяет наличие функций уровня SuperVIP.

    Args:
        access: Текущий статус доступа ('supervip', 'base', 'expired').

    Returns:
        bool: True, если тариф SuperVIP, иначе False.
    """
    return access == "supervip"


def current_access_deadline(
    chat: Chat | None, now: datetime | None = None
) -> datetime | None:
    """Возвращает дату и время окончания текущего периода доступа.

    Логика согласована с effective_access.
    Если доступ истёк или объект чата отсутствует — возвращает None.

    Args:
        chat: Объект чата из БД или None.
        now: Текущие дата и время (по умолчанию datetime.now(timezone.utc)).

    Returns:
        datetime | None: Время окончания подписки/триала в UTC или None.
    """
    if chat is None:
        return None
    if now is None:
        now = datetime.now(UTC)

    now_utc = _ensure_utc(now)
    sub_end = _ensure_utc(chat.subscription_ends_at)
    trial_end = _ensure_utc(chat.trial_ends_at)
    tier = (chat.subscription_tier or "trial").lower()

    if (
        tier in ("supervip", "base")
        and sub_end
        and now_utc
        and sub_end > now_utc
    ):
        return sub_end
    if trial_end and now_utc and trial_end > now_utc:
        return trial_end
    return None
