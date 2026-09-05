"""Константы биллингового сервиса подписок."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal


class SubscriptionTier(StrEnum):
    """Тарифы подписки системы."""

    TRIAL = "trial"
    BASE = "base"
    SUPERVIP = "supervip"

    @classmethod
    def paid_tiers(cls) -> set[str]:
        """Возвращает множество всех значений платных тарифов."""
        return {tier.value for tier in cls if tier != cls.TRIAL}


STATUS_EXPIRED = "expired"


class AuditScenario(StrEnum):
    """Сценарии биллинговых операций для аудита."""

    UPGRADE_FROM_TRIAL = "upgrade_from_trial"
    SAME_TIER_EXTENSION = "same_tier_extension"
    UPGRADE_DEFERRED = "upgrade_deferred"
    DOWNGRADE_DEFERRED = "downgrade_deferred"
    UPGRADE_CONVERT_NOW_PRORATE = "upgrade_convert_now_prorate"


# Тарифные ставки
SUBSCRIPTION_RATES: dict[SubscriptionTier, Decimal] = {
    SubscriptionTier.TRIAL: Decimal("0.00"),
    SubscriptionTier.BASE: Decimal("149.00"),
    SubscriptionTier.SUPERVIP: Decimal("249.00"),
}

# Расчётное количество дней в месяце для пропорционального биллинга
DAYS_IN_MONTH: Decimal = Decimal("30.0")

# Количество секунд в сутках
SECONDS_IN_DAY: Decimal = Decimal("86400")

# Допустимые режимы обновления подписки
UpgradeMode = Literal["convert_now", "deferred"]
