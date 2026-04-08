"""Проверка подписки чата: trial / base / supervip."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from ..db.init_db import Chat

Access = Literal["supervip", "base", "expired"]


def effective_access(chat: Chat | None, now: datetime | None = None) -> Access:
    """Базовые функции доступны при trial/base/supervip; supervip — только при supervip."""
    if now is None:
        now = datetime.utcnow()
    if chat is None:
        return "expired"
    sub_end = chat.subscription_ends_at
    trial_end = chat.trial_ends_at
    tier = (chat.subscription_tier or "trial").lower()

    if tier == "supervip" and sub_end and sub_end > now:
        return "supervip"
    if tier == "base" and sub_end and sub_end > now:
        return "base"
    if trial_end and trial_end > now:
        return "base"
    # оплаченный supervip мог быть записан иначе — проверяем срок
    if sub_end and sub_end > now and tier == "supervip":
        return "supervip"
    return "expired"


def has_base_features(access: Access) -> bool:
    return access != "expired"


def has_supervip(access: Access) -> bool:
    return access == "supervip"


def current_access_deadline(chat: Chat | None, now: datetime | None = None) -> datetime | None:
    """
    Дата/время окончания текущего периода доступа (trial или оплаченная подписка).
    Если доступ уже истёк или чата нет — None.
    Логика согласована с effective_access.
    """
    if chat is None:
        return None
    if now is None:
        now = datetime.utcnow()
    tier = (chat.subscription_tier or "trial").lower()
    sub_end = chat.subscription_ends_at
    trial_end = chat.trial_ends_at
    if tier == "supervip" and sub_end and sub_end > now:
        return sub_end
    if tier == "base" and sub_end and sub_end > now:
        return sub_end
    if trial_end and trial_end > now:
        return trial_end
    if sub_end and sub_end > now and tier == "supervip":
        return sub_end
    return None
